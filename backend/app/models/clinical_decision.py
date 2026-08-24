from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ClinicalDecision(Base):
    __tablename__ = "clinical_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), default="", server_default="", index=True)
    external_user_id: Mapped[str] = mapped_column(String(255), default="", server_default="", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    patient_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    patient_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_text: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    action_params: Mapped[dict] = mapped_column(JSON, nullable=False)
    safety_level: Mapped[str] = mapped_column(String(16), default="low", server_default="low")
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending", index=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "external_user_id", "idempotency_key", name="uq_clinical_decision_idempotency"),
    )
