from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OwnedEntry:
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    external_user_id: Mapped[str] = mapped_column(String(255), default="")
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasetFolder(OwnedEntry, Base):
    __tablename__ = "dataset_folders"
    __table_args__ = (UniqueConstraint("owner_id", "external_user_id", "name"),)


class Dataset(OwnedEntry, Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("folder_id", "name"),)
    folder_id: Mapped[str] = mapped_column(ForeignKey("dataset_folders.id"), index=True)
    description: Mapped[str] = mapped_column(String(1000), default="")


class DatasetFile(OwnedEntry, Base):
    __tablename__ = "dataset_files"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), index=True)
    modality: Mapped[str] = mapped_column(String(20))
    mime: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
