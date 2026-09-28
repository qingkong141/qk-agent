import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_online_deployment_does_not_probe_ollama(monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, 'LLM_PROVIDER', 'openai')
    monkeypatch.setattr(main.settings, 'EMBEDDING_PROVIDER', 'disabled')

    async def unexpected_probe():
        pytest.fail('Online deployment must not probe Ollama')

    monkeypatch.setattr(main, 'check_ollama_health', unexpected_probe)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get('/health')
        assert response.status_code == 200
        assert response.json()['llm_provider'] == 'openai'
        assert 'ollama' not in response.json()
