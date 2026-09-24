from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class StudioArtifact(Base):
    __tablename__ = "studio_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    external_user_id: Mapped[str] = mapped_column(String(255), default="")
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24))
    config: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    published_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
