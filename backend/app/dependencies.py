from typing import Annotated, Optional, TypedDict

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.user import User

security = HTTPBearer(auto_error=False)


class Principal(TypedDict):
    id: str
    email: str
    auth_type: str
    workspace: str
    external_user_id: str


async def authenticate_principal(
    db: AsyncSession,
    *,
    bearer_token: str | None = None,
    api_key: str | None = None,
    end_user_id: str | None = None,
) -> Principal:
    if api_key:
        result = await db.execute(
            select(User).where(User.api_key == api_key, User.is_active.is_(True))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 API Key")
        auth_type = "api_key"
    else:
        if not bearer_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证凭据")
        try:
            payload = jwt.decode(bearer_token, settings.SECRET_KEY, algorithms=["HS256"])
            user_id = payload.get("sub")
        except JWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效令牌") from exc
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效令牌")
        user = await db.get(User, user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效令牌")
        auth_type = "jwt"

    return {
        "id": user.id,
        "email": user.email,
        "auth_type": auth_type,
        "workspace": "default",
        "external_user_id": end_user_id.strip() if auth_type == "api_key" and end_user_id else "",
    }


async def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
    x_platform_token: Annotated[Optional[str], Header(alias="X-Platform-Token")] = None,
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
    x_end_user_id: Annotated[Optional[str], Header(alias="X-End-User-ID")] = None,
) -> dict:
    """Validate platform SSO, JWT, or API key credentials."""
    if x_platform_token:
        from app.platform_auth import authenticate_platform
        return await authenticate_platform(db, x_platform_token)
    return await authenticate_principal(
        db,
        bearer_token=credentials.credentials if credentials else None,
        api_key=x_api_key,
        end_user_id=x_end_user_id,
    )


CurrentUser = Annotated[dict, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]

# 临床角色等级
CLINICAL_ROLE_LEVELS = {
    "nurse": 1,
    "senior_nurse": 2,
    "doctor": 3,
    "admin": 99,
}


def require_clinical_role(min_role: str = "nurse"):
    """返回一个依赖，校验当前用户是否有指定临床角色"""

    async def _check(user: CurrentUser) -> dict:
        role = user.get("clinical_role", "nurse")
        required = CLINICAL_ROLE_LEVELS.get(min_role, 1)
        current = CLINICAL_ROLE_LEVELS.get(role, 0)
        if current < required:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要 {min_role} 及以上角色，当前角色为 {role}",
            )
        return user

    return _check
