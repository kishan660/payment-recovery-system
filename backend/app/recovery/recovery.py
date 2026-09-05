from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.models.order import Order
from app.models.recovery_log import RecoveryLog
from app.models.recovery_attempt import RecoveryAttempt
from app.providers.razorpay import (
    RazorpayProvider,
    RazorpayProviderError
)


MAX_RECOVERY_ATTEMPTS = 3


def recover_payment(
    payment_id: str,
    db: Session,
    simulate_failures: int = 0
):
    """
    Recover a payment using Razorpay API as the
    authoritative source of payment status.

    Recovery is allowed only when Razorpay confirms
    that the payment was captured successfully.
    """

    # --------------------------------------------------
    # 1. Find payment and lock the row
    # --------------------------------------------------

    payment = (
        db.query(Payment)
        .filter(
            Payment.payment_id == payment_id
        )
        .with_for_update()
        .first()
    )

    if not payment:

        log = RecoveryLog(
            payment_id=payment_id,
            previous_status=None,
            new_status=None,
            action="RECOVERY_FAILED",
            reason="Payment does not exist"
        )

        db.add(log)
        db.commit()

        return {
            "status": "NOT_FOUND",
            "payment_id": payment_id,
            "message": "Payment does not exist"
        }

    # --------------------------------------------------
    # 2. Payment already successful
    # --------------------------------------------------

    if payment.status == "SUCCESS":

        log = RecoveryLog(
            payment_id=payment_id,
            previous_status=payment.status,
            new_status=payment.status,
            action="RECOVERY_SKIPPED",
            reason="Payment is already successful"
        )

        db.add(log)
        db.commit()

        return {
            "status": "ALREADY_RECOVERED",
            "payment_id": payment_id,
            "current_status": payment.status,
            "message": "Payment is already successfully recovered; no new recovery attempt created"
        }

    # --------------------------------------------------
    # 3. Payment must be recoverable
    # --------------------------------------------------

    if payment.status != "PENDING":

        log = RecoveryLog(
            payment_id=payment_id,
            previous_status=payment.status,
            new_status=payment.status,
            action="RECOVERY_BLOCKED",
            reason=(
                f"Payment status {payment.status} "
                f"is not recoverable"
            )
        )

        db.add(log)
        db.commit()

        return {
            "status": "RECOVERY_BLOCKED",
            "payment_id": payment_id,
            "current_status": payment.status,
            "message": "Payment is not in a recoverable state"
        }

    # --------------------------------------------------
    # 4. Ask Razorpay for authoritative payment state
    # --------------------------------------------------

    try:

        provider = RazorpayProvider()

        provider_payment = provider.fetch_payment(
            payment_id
        )

    except RazorpayProviderError as error:

        log = RecoveryLog(
            payment_id=payment_id,
            previous_status=payment.status,
            new_status=payment.status,
            action="RECOVERY_BLOCKED",
            reason=(
                "Unable to verify payment with Razorpay: "
                f"{error}"
            )
        )

        db.add(log)
        db.commit()

        return {
            "status": "RECOVERY_BLOCKED",
            "payment_id": payment_id,
            "reason": "Unable to verify payment with Razorpay",
            "provider_error": str(error)
        }

    # --------------------------------------------------
    # 5. Extract provider state
    # --------------------------------------------------

    provider_status = provider_payment.get(
        "internal_status"
    )

    provider_amount_paise = provider_payment.get(
        "amount"
    )

    provider_currency = provider_payment.get(
        "currency"
    )

    # --------------------------------------------------
    # 6. Provider must confirm SUCCESS
    # --------------------------------------------------

    if provider_status != "SUCCESS":

        log = RecoveryLog(
            payment_id=payment_id,
            previous_status=payment.status,
            new_status=payment.status,
            action="RECOVERY_BLOCKED",
            reason=(
                f"Razorpay status is "
                f"{provider_status}, not SUCCESS"
            )
        )

        db.add(log)
        db.commit()

        return {
            "status": "RECOVERY_BLOCKED",
            "payment_id": payment_id,
            "database_status": payment.status,
            "provider_status": provider_status,
            "reason": (
                "Razorpay has not confirmed "
                "a successful payment"
            )
        }

    # --------------------------------------------------
    # 7. Verify amount
    # --------------------------------------------------

    database_amount_paise = int(
        payment.amount * 100
    )

    if provider_amount_paise != database_amount_paise:

        log = RecoveryLog(
            payment_id=payment_id,
            previous_status=payment.status,
            new_status=payment.status,
            action="RECOVERY_BLOCKED",
            reason=(
                "Amount mismatch between database "
                "and Razorpay"
            )
        )

        db.add(log)
        db.commit()

        return {
            "status": "RECOVERY_BLOCKED",
            "payment_id": payment_id,
            "database_amount": float(payment.amount),
            "provider_amount_paise": provider_amount_paise,
            "reason": "Payment amount mismatch"
        }

    # --------------------------------------------------
    # 8. Verify currency
    # --------------------------------------------------

    if provider_currency != payment.currency:

        log = RecoveryLog(
            payment_id=payment_id,
            previous_status=payment.status,
            new_status=payment.status,
            action="RECOVERY_BLOCKED",
            reason=(
                "Currency mismatch between database "
                "and Razorpay"
            )
        )

        db.add(log)
        db.commit()

        return {
            "status": "RECOVERY_BLOCKED",
            "payment_id": payment_id,
            "database_currency": payment.currency,
            "provider_currency": provider_currency,
            "reason": "Payment currency mismatch"
        }

    # --------------------------------------------------
    # 9. Count previous recovery attempts
    # --------------------------------------------------

    previous_attempts = (
        db.query(RecoveryAttempt)
        .filter(
            RecoveryAttempt.payment_id == payment_id
        )
        .count()
    )

    if previous_attempts >= MAX_RECOVERY_ATTEMPTS:

        log = RecoveryLog(
            payment_id=payment_id,
            previous_status=payment.status,
            new_status=payment.status,
            action="RECOVERY_FAILED",
            reason="Maximum recovery attempts reached"
        )

        db.add(log)
        db.commit()

        return {
            "status": "RECOVERY_FAILED",
            "payment_id": payment_id,
            "attempts": previous_attempts,
            "message": "Maximum recovery attempts reached"
        }

    # --------------------------------------------------
    # 10. Create recovery attempt
    # --------------------------------------------------

    attempt_number = previous_attempts + 1

    previous_status = payment.status

    # --------------------------------------------------
    # 11. Controlled failure simulation
    # --------------------------------------------------

    if attempt_number <= simulate_failures:

        failed_attempt = RecoveryAttempt(
            payment_id=payment_id,
            attempt_number=attempt_number,
            status="FAILED",
            error_message="Simulated recovery failure"
        )

        db.add(failed_attempt)

        log = RecoveryLog(
            payment_id=payment_id,
            previous_status=previous_status,
            new_status=previous_status,
            action="RECOVERY_ATTEMPT_FAILED",
            reason=(
                f"Simulated failure on "
                f"attempt {attempt_number}"
            )
        )

        db.add(log)
        db.commit()

        return {
            "status": "RETRY_REQUIRED",
            "payment_id": payment_id,
            "attempt_number": attempt_number,
            "payment_status": payment.status,
            "message": "Simulated recovery failure"
        }

    # --------------------------------------------------
    # 12. Perform recovery
    # --------------------------------------------------

    try:

        # Update payment
        payment.status = "SUCCESS"

        # Update related order
        order = (
            db.query(Order)
            .filter(
                Order.order_id == payment.order_id
            )
            .first()
        )

        if order:
            order.status = "PAID"

        # Record successful attempt
        attempt = RecoveryAttempt(
            payment_id=payment_id,
            attempt_number=attempt_number,
            status="SUCCESS",
            error_message=None
        )

        db.add(attempt)

        # Record recovery log
        log = RecoveryLog(
            payment_id=payment_id,
            previous_status=previous_status,
            new_status="SUCCESS",
            action="PAYMENT_RECOVERED",
            reason=(
                "Razorpay API confirmed captured payment; "
                f"recovered on attempt {attempt_number}"
            )
        )

        db.add(log)

        db.commit()

        return {
            "status": "RECOVERED",
            "payment_id": payment_id,
            "attempt_number": attempt_number,
            "payment_status": payment.status,
            "order_id": payment.order_id,
            "order_status": (
                order.status
                if order
                else None
            ),
            "provider_status": provider_status
        }

    except Exception as error:

        db.rollback()

        failed_attempt = RecoveryAttempt(
            payment_id=payment_id,
            attempt_number=attempt_number,
            status="FAILED",
            error_message=str(error)
        )

        db.add(failed_attempt)

        db.commit()

        return {
            "status": "RECOVERY_FAILED",
            "payment_id": payment_id,
            "attempt_number": attempt_number,
            "error": str(error)
        }
