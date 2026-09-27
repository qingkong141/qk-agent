from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped,mapped_column
from app.db.session import Base
from app.models.dataset import OwnedEntry


class ProtocolPlugin(OwnedEntry,Base):
    __tablename__='studio_protocol_plugins'
    revision: Mapped[int]=mapped_column(Integer,default=1)
    active_version_id: Mapped[str|None]=mapped_column(String(36),nullable=True)


class PluginVersion(Base):
    __tablename__='studio_plugin_versions'
    __table_args__=(UniqueConstraint('plugin_id','number'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    plugin_id: Mapped[str]=mapped_column(ForeignKey('studio_protocol_plugins.id'),index=True)
    number: Mapped[int]=mapped_column(Integer)
    settings: Mapped[dict]=mapped_column(JSON)
    digest: Mapped[str]=mapped_column(String(64))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())


class PluginMessage(Base):
    __tablename__='studio_plugin_messages'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    plugin_id: Mapped[str]=mapped_column(ForeignKey('studio_protocol_plugins.id'),index=True)
    version_id: Mapped[str]=mapped_column(ForeignKey('studio_plugin_versions.id'))
    result: Mapped[dict]=mapped_column(JSON)
    received_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
