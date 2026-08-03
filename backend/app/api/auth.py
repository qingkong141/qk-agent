import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, HTTPException, status
from jose import jwt
from sqlalchemy import select

from app.config import settings
from app.db.session import async_session
from app.models.user import User
from app.schemas.auth import TokenResponse, UserLogin, UserRegister, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: str, expires_delta: timedelta) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    return jwt.encode({"sub": user_id, "exp": expire}, settings.SECRET_KEY, algorithm="HS256")


@router.post("/register", response_model=UserResponse)
async def register(data: UserRegister):
    hashed = hash_password(data.password)
    async with async_session() as db:
        existing = await db.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="邮箱已注册")

        user = User(
            id=str(uuid.uuid4()),
            email=data.email,
            hashed_password=hashed,
            api_key=f"ma-{uuid.uuid4().hex[:24]}",
        )
        db.add(user)
        await db.commit()
        return UserResponse(id=user.id, email=user.email, is_active=user.is_active)


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin):
    async with async_session() as db:
        result = await db.execute(select(User).where(User.email == data.email))
        user = result.scalar_one_or_none()
        if not user or not verify_password(data.password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")

        access = create_token(user.id, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
        refresh = create_token(user.id, timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))
        return TokenResponse(access_token=access, refresh_token=refresh, user_id=user.id)
