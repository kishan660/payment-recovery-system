from sqlalchemy import Column, Integer, String, DateTime, Text, UniqueConstraint
from datetime import datetime

from app.database.database import Base


class RecoveryAttempt(Base):
    __tablename__ = "recovery_attempts"

    __table_args__ = (
        UniqueConstraint(
            "payment_id",
            "attempt_number",
            name="uq_payment_attempt"
        ),
    )

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    payment_id = Column(
        String,
        nullable=False,
        index=True
    )

    attempt_number = Column(
        Integer,
        nullable=False
    )

    status = Column(
        String,
        nullable=False
    )

    error_message = Column(
        Text,
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )