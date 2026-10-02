"""Single POST, bounded GET. No undocumented idempotency or cost assumptions."""
import asyncio
import json
import uuid
from urllib.parse import urlsplit

import httpx
from services.media.contracts import MediaProviderError, MAX_VIDEO_BYTES

BASE = 'https://api.creatomate.com/v2/renders'


def render_script(plan, urls):
    if len(urls) != 6:
        raise ValueError('six staged assets required')
    elements = [{'type': 'image', 'source': url, 'time': i * 7, 'duration': 7,
                 'fit': 'contain', 'track': 1} for i, url in enumerate(urls[:5])]
    elements.append({'type': 'audio', 'source': urls[5], 'time': 0, 'track': 2})
    # Preserve logical Unicode order; RTL shaping is a live qualification gate.
    elements += [{'type': 'text', 'text': c['text'], 'time': c['start_ms']/1000,
                  'duration': (c['end_ms']-c['start_ms'])/1000, 'track': 3,
                  'y': '80%', 'width': '85%', 'font_family': 'Noto Sans Hebrew',
                  'font_size': '5 vmin', 'fill_color': '#ffffff',
                  'stroke_color': '#000000', 'stroke_width': '0.3 vmin'}
                 for c in plan['captions']]
    return {'output_format': 'mp4', 'width': 720, 'height': 1280,
            'frame_rate': 30, 'duration': 35, 'elements': elements}


class CreatomateAdapter:
    def __init__(self, key, transport=None):
        self.key, self.transport = key, transport

    async def request(self, method, url, *, payload=None, limit=128000, auth=True):
        try:
            async with asyncio.timeout(90):
                async with httpx.AsyncClient(transport=self.transport, follow_redirects=False,
                        trust_env=False, timeout=30) as client:
                    async with client.stream(method, url, json=payload,
                            headers={'Authorization': 'Bearer '+self.key} if auth else {}) as response:
                        if response.status_code >= 300:
                            raise MediaProviderError('short_provider_http_error', submission_unknown=method == 'POST')
                        data = bytearray()
                        async for part in response.aiter_bytes():
                            data.extend(part)
                            if len(data) > limit:
                                raise MediaProviderError('short_provider_size_limit', submission_unknown=method == 'POST')
                        return bytes(data)
        except (httpx.HTTPError, TimeoutError):
            raise MediaProviderError('short_provider_transport', submission_unknown=method == 'POST') from None

    async def submit(self, plan, urls):
        if not self.key:
            raise MediaProviderError('short_provider_unconfigured')
        data = await self.request('POST', BASE, payload=render_script(plan, urls))
        try:
            rows = json.loads(data)
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError()
            return str(uuid.UUID(rows[0]['id']))
        except (ValueError, TypeError, KeyError):
            raise MediaProviderError('short_submission_unreadable', submission_unknown=True) from None

    async def poll(self, ref):
        ref = str(uuid.UUID(ref))
        try:
            row = json.loads(await self.request('GET', BASE+'/'+ref))
            if row['id'] != ref or row['status'] not in ('planned','waiting','transcribing','rendering','succeeded','failed'):
                raise ValueError()
            return row['status'], row.get('url') if row['status'] == 'succeeded' else None
        except (ValueError, KeyError, TypeError):
            raise MediaProviderError('short_poll_invalid') from None

    async def download(self, ref, url):
        p = urlsplit(url or '')
        # Fixed provider CDN; no arbitrary URLs, ports, credentials or redirects.
        if (p.scheme != 'https' or p.netloc != 'cdn.creatomate.com'
                or p.path != '/renders/'+str(uuid.UUID(ref))+'.mp4' or p.fragment or p.query):
            raise MediaProviderError('short_output_url_invalid')
        return await self.request('GET', url, limit=MAX_VIDEO_BYTES, auth=False)
