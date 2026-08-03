from app.config import settings


def get_vector_store_config() -> dict:
    return {
        "connection_string": settings.DATABASE_URL,
        "collection_name": "knowledge_base",
        "embedding_model": settings.EMBEDDING_MODEL,
    }
