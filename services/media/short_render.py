"""Managed fixed-plan render; all source authority remains in BEN."""
import asyncio
import hashlib
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from services.media.access import media_unavailable
from services.media.creatomate import CreatomateAdapter
from services.media.contracts import MediaProviderError
from services.media.narration import audio_bytes
from services.media.photo_source import normalize
from services.media.short_contract import ScenePlan
from services.media.short_staging import ShortStaging


async def load_assets(service, org, user, plan):
    async with service.repo.transaction(org) as s:
        await service.repo.destination(s, org, plan.conversation_id)
    assets = []
    for resource in plan.image_resource_ids:
        row = await service.repo.read(org, user, resource=resource)
        if row['state'] != 'succeeded' or row['mime_type'] != 'image/png':
            raise HTTPException(422, 'Ready BEN image required')
        original = await service.resource_bytes(org, user, resource)
        # Validate but stage original bytes, preserving the recorded checksum.
        await asyncio.to_thread(normalize, original)
        assets.append((original, 'image/png'))
    voice = await audio_bytes(service.repo, org, user, plan.narration_workspace_id,
        plan.narration_file_id, max_seconds=35, max_bytes=7_000_000)
    from services.media.local_composer import wav_duration
    if abs(float(wav_duration(voice, max_seconds=35, max_bytes=7_000_000))-35) > 0.1:
        raise HTTPException(422, 'Prepared narration must cover 35 seconds')
    assets.append((voice, 'audio/wav'))
    return assets


async def snapshot(service, org, user, plan):
    assets = await load_assets(service, org, user, plan)
    return {'normalization_version': 'short-render-v1', 'provider': 'creatomate',
        'model': 'fixed_5_scene_v1', 'operation': 'short_render',
        'destination': {'conversation_id': str(plan.conversation_id)},
        'plan': plan.model_dump(mode='json'),
        'input_checksums': [hashlib.sha256(data).hexdigest() for data, _ in assets],
        'parameters': {'aspect_ratio': '9:16', 'duration_seconds': 35,
                       'validation_profile': 'short-v1'}}


async def sources(service, row):
    p = row['request_payload']
    assets = await load_assets(service, row['org_id'], row['created_by'], ScenePlan(**p['plan']))
    if [hashlib.sha256(data).hexdigest() for data, _ in assets] != p['input_checksums']:
        raise HTTPException(409, 'Short render source changed')
    return assets


async def run_short(service, row):
    adapter = getattr(service, 'short_adapter', None) or CreatomateAdapter(os.getenv('CREATOMATE_API_KEY', ''))
    telemetry = dict(row.get('short_telemetry') or {})
    telemetry.update(request_version='short-render-v1', execution_profile='five-scenes-35s-portrait-v1',
                     render_adapter='creatomate', creation_id=str(row['execution_id']))
    now = datetime.now(timezone.utc)
    try:
        if row['state'] == 'pending':
            # Kill switch stops NEW submissions; known paid work can reconcile.
            if os.getenv('BEN_MEDIA_SHORT_RENDER_ENABLED') != '1':
                raise media_unavailable()
            if not getattr(adapter, 'key', None):
                raise ValueError('short_provider_unconfigured')
            assets = await sources(service, row)
            stage = getattr(service, 'short_stage', None) or ShortStaging()
            started = time.monotonic()
            urls = await asyncio.wait_for(asyncio.to_thread(stage.prepare, row['execution_id'], assets), timeout=180)
            telemetry['staging_duration_ms'] = round((time.monotonic()-started)*1000)
            telemetry['queue_latency_ms'] = round((now-row['created_at']).total_seconds()*1000)
            # Fencing rechecks deadline after staging, before any paid POST.
            row = await service.repo.mark_submitting(row)
            if row is None:
                return
            ref = await adapter.submit(row['request_payload']['plan'], urls)
            telemetry['submitted_at'] = datetime.now(timezone.utc).isoformat()
            await service.repo.change(row, state='submitted', provider_operation_ref=ref,
                short_telemetry=telemetry, next_reconcile_at=now+timedelta(seconds=10))
            return
        if row['state'] in ('submitting', 'submission_unknown'):
            await service.repo.change(row, state='submission_unknown', short_telemetry=telemetry,
                error_code='short_submission_uncertain', next_reconcile_at=row['deadline_at'])
            return
        if row['poll_attempts'] >= 100:
            await service.repo.change(row, state='expired', error_code='short_poll_limit')
            return
        status, url = await adapter.poll(row['provider_operation_ref'])
        if status == 'failed':
            await service.repo.change(row, state='failed', error_code='short_provider_failed', short_telemetry=telemetry)
            return
        if status != 'succeeded':
            await service.repo.change(row, state='running', provider_state=status,
                poll_attempts=row['poll_attempts']+1, short_telemetry=telemetry,
                next_reconcile_at=now+timedelta(seconds=min(30, 10+row['poll_attempts'])))
            return
        started = time.monotonic()
        data = await adapter.download(row['provider_operation_ref'], url)
        telemetry['download_duration_ms'] = round((time.monotonic()-started)*1000)
        await sources(service, row)
        from services.media.short_validation import validate
        started = time.monotonic()
        telemetry['ffprobe_check'] = await asyncio.to_thread(validate, data)
        telemetry['validation_duration_ms'] = round((time.monotonic()-started)*1000)
        telemetry['technical_pass'] = True
        if telemetry.get('submitted_at'):
            telemetry['execution_duration_ms'] = round((datetime.now(timezone.utc)-datetime.fromisoformat(telemetry['submitted_at'])).total_seconds()*1000)
        telemetry['retry_attempts'] = 0
        telemetry['output_sha256'] = hashlib.sha256(data).hexdigest()
        telemetry['total_duration_ms'] = round((datetime.now(timezone.utc)-row['created_at']).total_seconds()*1000)
        # JSON telemetry remains private; public usage keeps its established schema.
        row = await service.repo.short_ingesting(row, telemetry)
        if row:
            await service.publish_video_attempt(row, data, attempt_id=uuid.uuid4())
    except MediaProviderError as exc:
        if row['state'] in ('submitting','pending'):
            await service.repo.change(row, state='submission_unknown' if exc.submission_unknown else 'failed',
                error_code=exc.code, short_telemetry=telemetry, next_reconcile_at=row['deadline_at'])
        else:
            await service.repo.change(row, poll_attempts=row['poll_attempts']+1, error_code=exc.code,
                short_telemetry=telemetry, next_reconcile_at=now+timedelta(seconds=20))
    except (HTTPException, ValueError, OSError, TimeoutError):
        await service.repo.change(row, state='failed', error_code='short_validation_or_staging_failed', short_telemetry=telemetry)
