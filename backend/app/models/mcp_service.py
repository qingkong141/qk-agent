from sqlalchemy import Boolean, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.dataset import OwnedEntry


class MCPService(OwnedEntry, Base):
    __tablename__ = 'studio_mcp_services'
    description: Mapped[str] = mapped_column(String(1000), default='')
    category: Mapped[str] = mapped_column(String(30), default='other')
    url: Mapped[str] = mapped_column(String(1000))
    transport: Mapped[str] = mapped_column(String(30), default='streamable_http')
    auth_type: Mapped[str] = mapped_column(String(20), default='none')
    header_name: Mapped[str] = mapped_column(String(100), default='X-API-Key')
    query_name: Mapped[str] = mapped_column(String(100), default='key', server_default='key')
    stdio_profile: Mapped[str] = mapped_column(String(100), default='', server_default='')
    credential: Mapped[str] = mapped_column(Text, default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    checked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
