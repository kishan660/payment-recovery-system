from fastapi import FastAPI, Depends, HTTPException, Request, Header, Body
from sqlalchemy.orm import Session
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
from datetime import datetime
import uuid
import json
import hmac
import hashlib
import os
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


class RazorpayWebhookPayload(BaseModel):
    event_id: str
    event_type: str
    payment_id: str | None = None


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
# Razorpay webhook
# --------------------------------

@app.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None),
    payload: RazorpayWebhookPayload = Body(...),
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

    event_id = payload_data.get("event_id")
    event_type = payload_data.get("event_type")
    payment_id = payload_data.get("payment_id")

    if not event_id:

        raise HTTPException(
            status_code=400,
            detail="event_id is required"
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
    db.commit()
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
@app.post("/test/create-checkout-order")
def create_checkout_order(
    amount: int = 100000,
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
            "key_id": os.getenv("RAZORPAY_KEY_ID"),
            "order_id": result["order_id"],
            "amount": result["amount"],
            "currency": result["currency"]
        }

    except RazorpayProviderError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error)
        )
    