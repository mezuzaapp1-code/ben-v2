import pytest
import httpx
from tests.test_narration_api import app_for, identity


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', ['0', '1'])
async def test_narration_capability_follows_server_flag(monkeypatch, enabled):
    identity(monkeypatch)
    monkeypatch.setenv('BEN_MEDIA_LOCAL_NARRATION_ENABLED', enabled)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for()), base_url='http://test') as client:
        response = await client.get('/api/media/capabilities')
    assert response.status_code == 200
    assert response.json()['narration_replacement'] is (enabled == '1')


@pytest.mark.asyncio
async def test_narration_capability_hidden_outside_pilot(monkeypatch):
    identity(monkeypatch, user='outsider')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for()), base_url='http://test') as client:
        response = await client.get('/api/media/capabilities')
    assert response.status_code == 404
