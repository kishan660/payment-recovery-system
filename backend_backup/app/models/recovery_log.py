from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime

from app.database.database import Base


class RecoveryLog(Base):
    __tablename__ = "recovery_logs"

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

    previous_status = Column(
        String,
        nullable=True
    )

    new_status = Column(
        String,
        nullable=True
    )

    action = Column(
        String,
        nullable=False
    )

    reason = Column(
        Text,
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )