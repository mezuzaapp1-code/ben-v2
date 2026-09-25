"""Gate-0 characterization and strict, unresolved security acceptance tests.

No V2 policy implementation. No real DB, storage writes, or provider calls.
XFAIL means a confirmed release blocker, not a passed security requirement.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

from fastapi import HTTPException
import pytest

from services.workspace_files import service, initial_read
from services.workspace_files.file_resolver import eligible_from_row
from services.message_format import encode_chat_assistant
from services.rolling_context import build_rolling_context_prompt
from services.thread_service import ChatHistoryRow
import services.expert_opinion_service as opinion
import services.rolling_context as rolling
from services.workspace_files.source_policy import FILE_INITIAL_READ_EVENT

ORG = uuid.UUID('11111111-1111-1111-1111-111111111111')
OTHER_ORG = uuid.UUID('22222222-2222-2222-2222-222222222222')
PROJECT = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
FILE = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
THREAD = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')


def session_for(monkeypatch, module, row):
    session = SimpleNamespace(
        get=AsyncMock(return_value=row),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row)),
    )

    @asynccontextmanager
    async def context():
        yield session

    monkeypatch.setattr(module, 'get_db_session', context)
    return session


@pytest.mark.asyncio
async def test_legacy_project_lookup_retains_tenant_boundary(monkeypatch):
    session_for(monkeypatch, service, SimpleNamespace(id=PROJECT, org_id=OTHER_ORG))
    with pytest.raises(HTTPException) as error:
        await service._require_workspace(ORG, PROJECT)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_legacy_project_lookup_positive_baseline(monkeypatch):
    row = SimpleNamespace(id=PROJECT, org_id=ORG)
    session_for(monkeypatch, service, row)
    assert await service._require_workspace(ORG, PROJECT) is row


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason='V2 blocker: current Project access receives tenant, not private principal authority')
async def test_unresolved_private_project_is_not_authorized_by_tenant_alone(monkeypatch):
    # Real current Project has no owner/grant field. Do not invent one in the stub.
    session_for(monkeypatch, service, SimpleNamespace(id=PROJECT, org_id=ORG))
    try:
        await service._require_workspace(ORG, PROJECT)
    except HTTPException as error:
        assert error.status_code in (403, 404)
    else:
        assert False, 'tenant equality authorized a Project with unresolved private ownership'


@pytest.mark.parametrize('status,tenant,workspace,expected', [
    ('ready', ORG, PROJECT, True),
    ('queued', ORG, PROJECT, False),
    ('failed', ORG, PROJECT, False),
    ('ready', OTHER_ORG, PROJECT, False),
    ('ready', ORG, uuid.UUID(int=99), False),
])
def test_existing_text_evidence_eligibility(status, tenant, workspace, expected):
    row = SimpleNamespace(id=FILE, org_id=tenant, workspace_id=workspace,
                          status=status, extracted_text='bounded source', display_name='A.txt')
    assert (eligible_from_row(row, ORG, PROJECT) is not None) is expected


@pytest.mark.asyncio
async def test_missing_file_denies_bytes_before_storage(monkeypatch):
    monkeypatch.setattr(service, '_require_workspace', AsyncMock())
    session_for(monkeypatch, service, None)

    def forbidden_storage(*args):
        raise AssertionError('missing resource reached storage')

    monkeypatch.setattr(service.storage, 'absolute_path_for_key', forbidden_storage)
    with pytest.raises(HTTPException) as error:
        await service.open_file_bytes(org_id=ORG, workspace_id=PROJECT, file_id=FILE)
    assert error.value.status_code == 404


def test_ordinary_answer_remains_conversation_content():
    # Source citation is not transitive revocation under the accepted contract.
    message = encode_chat_assistant('Previously authorized answer', model_used='test',
                                   cost_usd=0, provider_id='gpt',
                                   used_files=[{'id': str(FILE), 'name': 'A.txt'}])
    prompt = build_rolling_context_prompt([ChatHistoryRow('assistant', message)],
                                         opinion_request='Continue')
    assert 'Previously authorized answer' in prompt
    assert prompt.endswith('Continue')


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason='V2 blocker: Initial Read history is replayed without current source authorization')
async def test_deleted_initial_read_is_not_replayed_as_ordinary_history(monkeypatch):
    message = encode_chat_assistant('SECRET_DIRECT_SOURCE', model_used='test',
                                   cost_usd=0, provider_id='gpt',
                                   source_event=FILE_INITIAL_READ_EVENT,
                                   source_file_id=str(FILE))
    monkeypatch.setattr(rolling, '_load_chat_history_messages',
                        AsyncMock(return_value=[ChatHistoryRow('assistant', message)]))
    # Authorized conversation still exists, but the source record does not.
    session_for(monkeypatch, service, None)
    prompt = await rolling.build_rolling_stream_prompt(ORG, THREAD, 'Continue')
    assert 'SECRET_DIRECT_SOURCE' not in prompt


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason='Gate 1 blocker: anchored opinion reads SQLite before thread authorization')
async def test_anchored_opinion_authorizes_before_sqlite_read(monkeypatch):
    def forbidden_history(*args):
        raise AssertionError('unvalidated destination reached SQLite history')

    monkeypatch.setattr(opinion, 'list_thread_messages_until', forbidden_history)
    generator = opinion.stream_expert_opinion(
        OTHER_ORG, THREAD, session_id=uuid.UUID(int=1), provider_id='gpt',
        tenant_id=str(OTHER_ORG), tier='free', anchor_message_id=1,
    )
    try:
        with pytest.raises(HTTPException) as error:
            await anext(generator)
        assert error.value.status_code in (403, 404)
    finally:
        await generator.aclose()


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason='Gate 1 blocker: Initial Read trusts source_chat_id before destination authorization')
async def test_initial_read_authorizes_destination_before_history(monkeypatch):
    row = SimpleNamespace(id=FILE, org_id=ORG, media_type='text/plain',
                          original_filename='A.txt', source_chat_id=str(THREAD), status='ready')
    session_for(monkeypatch, initial_read, row)

    def forbidden_history(*args):
        raise AssertionError('unvalidated source_chat_id reached SQLite history')

    monkeypatch.setattr(initial_read, 'sqlite_has_initial_read', forbidden_history)
    with pytest.raises(HTTPException) as error:
        await initial_read.claim_initial_read(ORG, FILE)
    assert error.value.status_code in (403, 404)
