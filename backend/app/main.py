from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api import (
    agents, auth, chat, clinical_decisions, clinical_events,
    context, conversations, documents, monitor, runtime, studio, tools, workflows,
)
from app.api import semantic, syntax, protocol_debug, datasets, modeling, offline
from app.config import settings
from app.core.exceptions import (
    global_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.core.langsmith_setup import setup_langsmith
from app.core.logging_setup import setup_logging
from app.db.session import ensure_schema_patches, init_db
from app.middleware.rate_limit import limiter
from app.llm.ollama_health import check_ollama_health
from app.tools import register_default_tools
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    setup_langsmith()
    logger.info(f"Starting {settings.APP_NAME}")
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.LOG_DIR).mkdir(parents=True, exist_ok=True)
    await init_db()
    await ensure_schema_patches()
    register_default_tools()
    if settings.LLM_PROVIDER == "ollama" or settings.EMBEDDING_PROVIDER == "ollama":
        ollama_status = await check_ollama_health()
        logger.info(f"Ollama: {settings.OLLAMA_BASE_URL} reachable={ollama_status.get('reachable')}")
        if not ollama_status.get("reachable"):
            logger.warning("Ollama 不可达，对话/知识库将无法工作。请启动 Ollama 并 pull 所需模型。")
        elif not ollama_status.get("llm_model_ok"):
            logger.warning(f"Ollama 缺少对话模型，请执行: ollama pull {settings.LLM_MODEL}")
        elif not ollama_status.get("embedding_model_ok"):
            logger.warning(f"Ollama 缺少 Embedding 模型，请执行: ollama pull {settings.EMBEDDING_MODEL}")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

prefix = settings.API_V1_PREFIX
app.include_router(auth.router, prefix=prefix)
app.include_router(conversations.router, prefix=prefix)
app.include_router(chat.router, prefix=prefix)
app.include_router(documents.router, prefix=prefix)
app.include_router(agents.router, prefix=prefix)
app.include_router(context.router, prefix=prefix)
app.include_router(tools.router, prefix=prefix)
app.include_router(monitor.router, prefix=prefix)
app.include_router(workflows.router, prefix=prefix)
app.include_router(clinical_decisions.router, prefix=prefix)
app.include_router(clinical_events.router, prefix=prefix)
app.include_router(runtime.router, prefix=prefix)
app.include_router(studio.router, prefix=prefix)
app.include_router(semantic.router, prefix=prefix)
app.include_router(syntax.router, prefix=prefix)
app.include_router(protocol_debug.router, prefix=prefix)
app.include_router(datasets.router, prefix=prefix)
app.include_router(modeling.router, prefix=prefix)
app.include_router(offline.router, prefix=prefix)


@app.get("/health")
@limiter.exempt
async def health(request: Request):
    body: dict = {"status": "ok", "app": settings.APP_NAME, "llm_provider": settings.LLM_PROVIDER}
    if settings.LLM_PROVIDER == "ollama" or settings.EMBEDDING_PROVIDER == "ollama":
        body["ollama"] = await check_ollama_health()
    return body
