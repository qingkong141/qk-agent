from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "AI Agent"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: str = "*"

    DATABASE_URL: str = "sqlite+aiosqlite:///./data/agent.db"

    LLM_PROVIDER: str = "anthropic"
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str = ""  # 内网 OpenAI 兼容网关，如 http://10.0.0.50:8080/v1
    DASHSCOPE_API_KEY: str = ""
    LLM_MODEL: str = "claude-sonnet-4-20250514"
    # 🆕 Multi-Agent 模型分层：每个 Agent 可选独立模型，为空则用 LLM_MODEL
    EDUCATION_AGENT_MODEL: str = ""   # 宣教推荐（低风险，可配轻量模型）
    INFUSION_AGENT_MODEL: str = ""    # 输液调整（高风险，建议配强模型）
    EMBEDDING_PROVIDER: str = ""  # 留空自动推断；ollama | openai | dashscope
    EMBEDDING_MODEL: str = "nomic-embed-text"
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    MAX_AGENT_ITERATIONS: int = 10
    MAX_EXECUTION_TIME: int = 120
    TEMPERATURE: float = 0.7
    MAX_TOKENS: int = 4096
    MAX_TOKENS_PER_CONVERSATION: int = 100_000

    CHUNK_SIZE: int = 800
    CHUNK_OVERLAP: int = 200
    RAG_TOP_K: int = 5
    RAG_SIMILARITY_THRESHOLD: float = 0.55
    RAG_HIGH_CONFIDENCE_SCORE: float = 0.72

    RATE_LIMIT_PER_MINUTE: int = 30

    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "ai-agent"

    UPLOAD_DIR: str = "./data/documents"
    LOG_DIR: str = "./data/logs"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    @property
    def cors_origin_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
