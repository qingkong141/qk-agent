from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.dataset import OwnedEntry


class ThemeDomain(OwnedEntry, Base):
    __tablename__ = "theme_domains"
    __table_args__ = (UniqueConstraint("owner_id", "external_user_id", "name"),)
    description: Mapped[str] = mapped_column(String(1000), default="")


class DataModel(OwnedEntry, Base):
    __tablename__ = "data_models"
    __table_args__ = (UniqueConstraint("domain_id", "table_name"), UniqueConstraint("domain_id", "name"))
    domain_id: Mapped[str] = mapped_column(ForeignKey("theme_domains.id"), index=True)
    table_name: Mapped[str] = mapped_column(String(64))
    layer: Mapped[str] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(String(1000), default="")
    fields: Mapped[list] = mapped_column(JSON)
    source_file_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_files.id"), index=True, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
