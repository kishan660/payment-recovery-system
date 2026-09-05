from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime

from app.database.database import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    event_id = Column(
        String,
        unique=True,
        index=True,
        nullable=False
    )

    event_type = Column(
        String,
        nullable=False
    )

    payment_id = Column(
        String,
        nullable=True,
        index=True
    )

    payload = Column(
        Text,
        nullable=False
    )

    status = Column(
        String,
        default="RECEIVED",
        nullable=False
    )

    received_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    processed_at = Column(
        DateTime,
        nullable=True
    )