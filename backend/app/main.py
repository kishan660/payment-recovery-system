from fastapi import FastAPI, Depends, HTTPException, Request, Header
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel

from datetime import datetime
import uuid
import json
import hmac
import hashlib
import os
from pathlib import Path
import threading
import time

from app.database.database import engine, Base, SessionLocal

from app.models.order import Order
from app.models.payment import Payment
from app.models.webhook_event import WebhookEvent
from app.models.recovery_log import RecoveryLog
from app.models.recovery_attempt import RecoveryAttempt
from app.models.recovery_job import RecoveryJob
from app.providers.razorpay import (RazorpayProvider,RazorpayProviderError)


from app.reconciliation.reconciliation import reconcile_payment
from app.recovery.recovery import recover_payment

from app.workers.recovery_worker import process_recovery_jobs
from app.workers.reconciliation_worker import process_reconciliation_jobs


# --------------------------------
# Create database tables
# --------------------------------

Base.metadata.create_all(bind=engine)


# --------------------------------
# Razorpay webhook secret
# --------------------------------

RAZORPAY_WEBHOOK_SECRET = os.getenv(
    "RAZORPAY_WEBHOOK_SECRET",
    "dev_webhook_secret"
)


DASHBOARD_PATH = (
    Path(__file__).resolve().parents[1]
    / "dashboard.html"
)


# --------------------------------
# FastAPI application
# --------------------------------

app = FastAPI(
    title="Payment Reconciliation & Recovery System",
    description="Fault-Tolerant Payment Reconciliation and Recovery System for Razorpay Transactions",
    version="1.0.0"
)


# --------------------------------
# Database dependency
# --------------------------------

def get_db():
    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()


# --------------------------------
# Request models
# --------------------------------

class OrderRequest(BaseModel):
    amount: float
    currency: str = "INR"


class TestPaymentRequest(BaseModel):
    order_id: str | None = None
    amount: float | None = None
    currency: str | None = None
    reset_history: bool = True


# --------------------------------
# Webhook signature verification
# --------------------------------

def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str
) -> bool:

    expected_signature = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(
        expected_signature,
        signature
    )


# --------------------------------
# Background worker
# --------------------------------

def recovery_worker_loop():

    while True:

        try:

            # Reconciliation scan
            process_reconciliation_jobs()

            # Recovery jobs
            db = SessionLocal()

            try:
                process_recovery_jobs(db)

            finally:
                db.close()

        except Exception as error:

            print(
                "Background worker error:",
                error
            )

        time.sleep(10)


# --------------------------------
# Start background worker
# --------------------------------

if os.getenv("ENABLE_BACKGROUND_WORKER", "0") == "1":
    worker_thread = threading.Thread(
        target=recovery_worker_loop,
        daemon=True
    )

    worker_thread.start()


# --------------------------------
# Root endpoint
# --------------------------------

@app.get("/")
def root():

    return {
        "message": "Payment Recovery System is running",
        "status": "healthy"
    }


@app.get("/dashboard")
def dashboard():

    if not DASHBOARD_PATH.exists():

        raise HTTPException(
            status_code=404,
            detail="Dashboard file not found"
        )

    return FileResponse(DASHBOARD_PATH)


# --------------------------------
# Create order
# --------------------------------

@app.post("/orders")
def create_order(
    order: OrderRequest,
    db: Session = Depends(get_db)
):

    order_id = "ORD_" + str(uuid.uuid4())[:8]

    new_order = Order(
        order_id=order_id,
        amount=order.amount,
        currency=order.currency,
        status="CREATED"
    )

    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    return {
        "order_id": new_order.order_id,
        "amount": float(new_order.amount),
        "currency": new_order.currency,
        "status": new_order.status
    }


# --------------------------------
# Create local pending payment for recovery demo
# --------------------------------

@app.post("/test/payments/{payment_id}/pending")
def create_test_pending_payment(
    payment_id: str,
    request: TestPaymentRequest,
    db: Session = Depends(get_db)
):
    """
    Test-only setup endpoint for recovery demos.
    Creates a local PENDING record for an existing Razorpay payment.
    """

    try:
        provider = RazorpayProvider()

        provider_payment = provider.fetch_payment(
            payment_id
        )

    except RazorpayProviderError as error:

        raise HTTPException(
            status_code=502,
            detail=str(error)
        )

    provider_amount_paise = provider_payment.get("amount")

    if request.amount is None and provider_amount_paise is None:

        raise HTTPException(
            status_code=400,
            detail="Payment amount is required"
        )

    amount = (
        request.amount
        if request.amount is not None
        else provider_amount_paise / 100
    )

    currency = (
        request.currency
        or provider_payment.get("currency")
        or "INR"
    )

    order_id = (
        request.order_id
        or provider_payment.get("order_id")
        or f"ORD_{payment_id[-8:]}"
    )

    order = db.query(Order).filter(
        Order.order_id == order_id
    ).first()

    if not order:

        order = Order(
            order_id=order_id,
            amount=amount,
            currency=currency,
            status="CREATED"
        )

        db.add(order)

    else:

        order.amount = amount
        order.currency = currency
        order.status = "CREATED"

    payment = db.query(Payment).filter(
        Payment.payment_id == payment_id
    ).first()

    if not payment:

        payment = Payment(
            payment_id=payment_id,
            order_id=order_id,
            amount=amount,
            currency=currency,
            status="PENDING",
            provider="RAZORPAY"
        )

        db.add(payment)

    else:

        payment.order_id = order_id
        payment.amount = amount
        payment.currency = currency
        payment.status = "PENDING"
        payment.provider = "RAZORPAY"

    if request.reset_history:

        db.query(RecoveryAttempt).filter(
            RecoveryAttempt.payment_id == payment_id
        ).delete()

        db.query(RecoveryJob).filter(
            RecoveryJob.payment_id == payment_id
        ).delete()

    db.commit()

    return {
        "status": "PENDING_CREATED",
        "payment_id": payment_id,
        "order_id": order_id,
        "amount": float(amount),
        "currency": currency,
        "payment_status": "PENDING",
        "provider_status": provider_payment.get("internal_status"),
        "history_reset": request.reset_history
    }


# --------------------------------
# Razorpay webhook
# --------------------------------

@app.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None),
    x_razorpay_event_id: str = Header(None),
    db: Session = Depends(get_db)
):
    
    # --------------------------------
    # Read raw request body
    # --------------------------------

    raw_body = await request.body()

    # --------------------------------
    # Get Razorpay signature
    # --------------------------------
    signature = x_razorpay_signature

    if not signature:

        raise HTTPException(
            status_code=401,
            detail="Missing Razorpay webhook signature"
        )

    # --------------------------------
    # Verify signature
    # --------------------------------

    if not verify_webhook_signature(
        raw_body,
        signature,
        RAZORPAY_WEBHOOK_SECRET
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid Razorpay webhook signature"
        )

    # --------------------------------
    # Parse JSON
    # --------------------------------

    try:

        payload_data = json.loads(raw_body)

    except json.JSONDecodeError:

        raise HTTPException(
            status_code=400,
            detail="Invalid JSON payload"
        )

    # --------------------------------
    # Extract event information
    # --------------------------------

    payment_entity = (
        payload_data.get("payload", {})
        .get("payment", {})
        .get("entity", {})
    )

    event_id = (
        x_razorpay_event_id
        or payload_data.get("event_id")
        or payload_data.get("id")
    )

    event_type = (
        payload_data.get("event_type")
        or payload_data.get("event")
    )

    payment_id = (
        payload_data.get("payment_id")
        or payment_entity.get("id")
    )

    if not event_id:

        raise HTTPException(
            status_code=400,
            detail="event_id or x-razorpay-event-id is required"
        )

    if not event_type:

        raise HTTPException(
            status_code=400,
            detail="event_type is required"
        )

    # --------------------------------
    # Idempotency check
    # --------------------------------

    existing_event = db.query(WebhookEvent).filter(
        WebhookEvent.event_id == event_id
    ).first()

    if existing_event:

        return {
            "message": "Event already received",
            "event_id": event_id,
            "status": existing_event.status
        }

    # --------------------------------
    # Store webhook event
    # --------------------------------

    webhook_event = WebhookEvent(
        event_id=event_id,
        event_type=event_type,
        payment_id=payment_id,
        payload=json.dumps(payload_data),
        status="RECEIVED"
    )

    db.add(webhook_event)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()

        existing_event = db.query(WebhookEvent).filter(
            WebhookEvent.event_id == event_id
        ).first()

        return {
            "message": "Event already received",
            "event_id": event_id,
            "status": (
                existing_event.status
                if existing_event
                else "RECEIVED"
            )
        }

    db.refresh(webhook_event)

    # --------------------------------
    # Find payment
    # --------------------------------

    payment = None

    if payment_id:

        payment = db.query(Payment).filter(
            Payment.payment_id == payment_id
        ).first()

    # --------------------------------
    # Payment not found
    # --------------------------------

    if not payment:

        webhook_event.status = "UNMATCHED"

        db.commit()

        return {
            "message": "Payment not found",
            "event_id": event_id,
            "payment_id": payment_id,
            "status": "UNMATCHED"
        }

    # --------------------------------
    # Update payment status
    # --------------------------------

    if event_type == "payment.captured":

        payment.status = "SUCCESS"

        order = db.query(Order).filter(
            Order.order_id == payment.order_id
        ).first()

        if order:
            order.status = "PAID"

    elif event_type == "payment.failed":

        payment.status = "FAILED"

    elif event_type == "payment.authorized":

        payment.status = "AUTHORIZED"

    else:

        payment.status = "UNKNOWN"

    # --------------------------------
    # Mark webhook processed
    # --------------------------------

    webhook_event.status = "PROCESSED"

    webhook_event.processed_at = datetime.utcnow()

    db.commit()

    return {
        "message": "Webhook processed successfully",
        "event_id": event_id,
        "payment_id": payment.payment_id,
        "payment_status": payment.status,
        "webhook_status": webhook_event.status
    }


# --------------------------------
# Reconciliation endpoint
# --------------------------------

@app.get("/reconcile/{payment_id}")
def reconcile(
    payment_id: str,
    db: Session = Depends(get_db)
):

    return reconcile_payment(
        payment_id,
        db
    )


# --------------------------------
# Manual recovery endpoint
# --------------------------------
@app.post("/test/recovery/{payment_id}")
def test_recovery(
    payment_id: str,
    simulate_failures: int = 1,
    db: Session = Depends(get_db)
):
    """
    Test-only endpoint for fault injection.

    simulate_failures=1 means the first recovery
    attempt will fail in a controlled way.
    """

    result = recover_payment(
        payment_id=payment_id,
        db=db,
        simulate_failures=simulate_failures
    )

    return result


# --------------------------------
# Queue recovery
# --------------------------------

@app.post("/recovery/queue/{payment_id}")
def queue_recovery(
    payment_id: str,
    db: Session = Depends(get_db)
):

    # Check payment
    payment = db.query(Payment).filter(
        Payment.payment_id == payment_id
    ).first()

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment does not exist"
        )

    # Check existing job
    existing_job = db.query(RecoveryJob).filter(
        RecoveryJob.payment_id == payment_id
    ).first()

    if existing_job:

        return {
            "status": "ALREADY_QUEUED",
            "payment_id": payment_id,
            "job_status": existing_job.status,
            "retry_count": existing_job.retry_count
        }

    # Create recovery job
    job = RecoveryJob(
        payment_id=payment_id,
        status="QUEUED",
        retry_count=0,
        max_retries=3
    )

    db.add(job)
    db.commit()
    db.refresh(job)

    return {
        "status": "QUEUED",
        "payment_id": payment_id,
        "job_id": job.id,
        "job_status": job.status,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries
    }


# --------------------------------
# Recovery history
# --------------------------------

@app.get("/recovery/history/{payment_id}")
def recovery_history(
    payment_id: str,
    db: Session = Depends(get_db)
):

    attempts = (
        db.query(RecoveryAttempt)
        .filter(
            RecoveryAttempt.payment_id == payment_id
        )
        .order_by(
            RecoveryAttempt.attempt_number.asc()
        )
        .all()
    )

    logs = (
        db.query(RecoveryLog)
        .filter(
            RecoveryLog.payment_id == payment_id
        )
        .order_by(
            RecoveryLog.created_at.asc()
        )
        .all()
    )

    return {
        "payment_id": payment_id,
        "attempts": [
            {
                "id": attempt.id,
                "attempt_number": attempt.attempt_number,
                "status": attempt.status,
                "error_message": attempt.error_message,
                "created_at": attempt.created_at
            }
            for attempt in attempts
        ],
        "logs": [
            {
                "id": log.id,
                "previous_status": log.previous_status,
                "new_status": log.new_status,
                "action": log.action,
                "reason": log.reason,
                "created_at": log.created_at
            }
            for log in logs
        ]
    }


# --------------------------------
# Recovery job status
# --------------------------------

@app.get("/recovery/jobs/{payment_id}")
def recovery_job_status(
    payment_id: str,
    db: Session = Depends(get_db)
):

    job = (
        db.query(RecoveryJob)
        .filter(
            RecoveryJob.payment_id == payment_id
        )
        .first()
    )

    if not job:

        return {
            "status": "NOT_FOUND",
            "payment_id": payment_id,
            "message": "Recovery job does not exist"
        }

    return {
        "status": job.status,
        "payment_id": payment_id,
        "job_id": job.id,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
        "next_retry_at": job.next_retry_at,
        "last_error": job.last_error,
        "created_at": job.created_at,
        "updated_at": job.updated_at
    }


@app.get("/provider/payments/{payment_id}")
def fetch_provider_payment(
    payment_id: str
):
    try:
        provider = RazorpayProvider()

        result = provider.fetch_payment(
            payment_id
        )

        return {
            "status": "SUCCESS",
            "source": "RAZORPAY_API",
            "payment": result
        }

    except RazorpayProviderError as error:

        raise HTTPException(
            status_code=502,
            detail=str(error)
        )

@app.post("/provider/orders")
def create_provider_order(
    amount: int,
    currency: str = "INR"
):
    try:
        provider = RazorpayProvider()

        result = provider.create_order(
            amount=amount,
            currency=currency
        )

        return {
            "status": "SUCCESS",
            "source": "RAZORPAY_API",
            "order": result
        }

    except RazorpayProviderError as error:

        raise HTTPException(
            status_code=502,
            detail=str(error)
        )

    
