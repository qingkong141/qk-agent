from sqlalchemy import ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.dataset import OwnedEntry
from app.db.session import Base


class MasterIndex(OwnedEntry, Base):
    __tablename__ = 'studio_master_indices'
    kind: Mapped[str] = mapped_column(String(10))
    code: Mapped[str] = mapped_column(String(40), unique=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class MasterAlias(Base):
    __tablename__ = 'studio_master_aliases'
    __table_args__ = (UniqueConstraint('owner_id', 'external_user_id', 'kind', 'system', 'external_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    master_id: Mapped[str] = mapped_column(ForeignKey('studio_master_indices.id'), index=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    external_user_id: Mapped[str] = mapped_column(String(255), default='')
    kind: Mapped[str] = mapped_column(String(10))
    system: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(160))
    attributes: Mapped[dict] = mapped_column(JSON)
