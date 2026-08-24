from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RunCheckpoint(Base):
    __tablename__ = "run_checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    external_user_id: Mapped[str] = mapped_column(String(255), default="", server_default="")
    sequence: Mapped[int]
    phase: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_run_checkpoint_sequence"),)


class ToolApproval(Base):
    __tablename__ = "tool_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    external_user_id: Mapped[str] = mapped_column(String(255), default="", server_default="")
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    arguments_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    requested_arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "external_user_id", "conversation_id", "tool_name", "arguments_hash",
            name="uq_tool_approval_request",
        ),
    )


class UserContext(Base):
    __tablename__ = "user_contexts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    external_user_id: Mapped[str] = mapped_column(String(255), default="", server_default="")
    workspace: Mapped[str] = mapped_column(String(64), default="default")
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "external_user_id", "workspace", name="uq_user_context_scope"),
    )
