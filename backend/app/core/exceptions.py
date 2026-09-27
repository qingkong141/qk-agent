from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from loguru import logger
from app.config import settings
from starlette.exceptions import HTTPException as StarletteHTTPException


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": f"HTTP_{exc.status_code}"},
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if request.url.path.removeprefix(settings.API_V1_PREFIX).startswith('/studio/mcp-services'):
        # Model-level validation errors can otherwise echo the complete credential form.
        errors = [{key:error[key] for key in ('type','loc','msg') if key in error} for error in errors]
    return JSONResponse(
        status_code=422,
        content={"detail": "请求参数无效", "errors": jsonable_encoder(errors), "code": "VALIDATION_ERROR"},
    )


async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试", "code": "INTERNAL_ERROR"},
    )
