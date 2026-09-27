from sqlalchemy import Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base
from app.models.dataset import OwnedEntry


class DataSource(OwnedEntry, Base):
    __tablename__='studio_data_sources'
    connection: Mapped[dict]=mapped_column(JSON)
    credential: Mapped[str]=mapped_column(Text,default='')
    revision: Mapped[int]=mapped_column(Integer,default=1)
    last_test: Mapped[dict]=mapped_column(JSON,default=dict)
