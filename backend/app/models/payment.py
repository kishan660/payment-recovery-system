
from sqlalchemy import Column, Integer, String, Numeric, DateTime
from datetime import datetime

from app.database.database import Base


class Payment(Base):
    __tablename__ = "payments"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    payment_id = Column(
        String,
        unique=True,
        index=True,
        nullable=False
    )

    order_id = Column(
        String,
        nullable=False,
        index=True
    )

    amount = Column(
        Numeric(12, 2),
        nullable=False
    )

    currency = Column(
        String(3),
        default="INR",
        nullable=False
    )

    status = Column(
        String,
        default="PENDING",
        nullable=False
    )

    provider = Column(
        String,
        default="RAZORPAY",
        nullable=False
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )