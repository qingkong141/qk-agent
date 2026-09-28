from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PublicationAccess(Base):
    __tablename__ = 'studio_publication_access'

    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    credentials: Mapped[str] = mapped_column(Text, default='')
    auth_type: Mapped[str] = mapped_column(String(24), default='platform')
