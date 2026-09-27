from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.dataset import OwnedEntry


class AgentModel(OwnedEntry, Base):
    __tablename__ = 'studio_agent_models'
    base_url: Mapped[str] = mapped_column(String(1000))
    model: Mapped[str] = mapped_column(String(150))
    credential: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
