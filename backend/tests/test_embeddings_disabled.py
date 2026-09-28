import pytest

from app.llm import embeddings_factory


def test_disabled_embeddings_do_not_fall_back_to_cloud(monkeypatch):
    monkeypatch.setattr(embeddings_factory.settings, 'EMBEDDING_PROVIDER', 'disabled')
    monkeypatch.setattr(embeddings_factory.settings, 'OPENAI_API_KEY', 'configured-cloud-key')
    monkeypatch.setattr(embeddings_factory.settings, 'LLM_PROVIDER', 'openai')
    assert embeddings_factory._resolve_provider() == 'disabled'
    with pytest.raises(ValueError, match='未启用文档向量检索'):
        embeddings_factory.create_embeddings()
