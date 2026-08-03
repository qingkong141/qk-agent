from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.user import User

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
) -> dict:
    """JWT 或 API Key 双模式认证"""
    if x_api_key:
        result = await db.execute(
            select(User).where(User.api_key == x_api_key, User.is_active.is_(True))
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 API Key")
        return {
            "id": user.id,
            "email": user.email,
            "auth_type": "api_key",
            "workspace": "default",
        }

    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证凭据")

    try:
        payload = jwt.decode(credentials.credentials, settings.SECRET_KEY, algorithms=["HS256"])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效令牌")
        return {"id": user_id, "auth_type": "jwt", "workspace": "default"}
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效令牌")


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
