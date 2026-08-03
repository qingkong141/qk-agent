from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings


def _rate_limit_key(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"apikey:{api_key[:24]}"
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return f"jwt:{auth[7:27]}"
    return get_remote_address(request)


limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)
