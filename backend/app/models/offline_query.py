from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.dataset import OwnedEntry


class OfflineQuery(OwnedEntry, Base):
    __tablename__ = "offline_queries"
    __table_args__ = (UniqueConstraint("domain_id", "name"),)
    domain_id: Mapped[str] = mapped_column(ForeignKey("theme_domains.id"), index=True)
    model_ids: Mapped[list] = mapped_column(JSON)
    sql: Mapped[str] = mapped_column(Text)
    row_limit: Mapped[int] = mapped_column(Integer, default=1000)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
