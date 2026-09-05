from decimal import Decimal
from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.providers.razorpay import (
    RazorpayProvider,
    RazorpayProviderError
)


def reconcile_payment(
    payment_id: str,
    db: Session
):
    """
    Compare our local payment record with
    Razorpay's current payment state.

    Reconciliation is READ-ONLY.
    It does not modify payment status.
    """

    # 1. Find local payment
    payment = (
        db.query(Payment)
        .filter(
            Payment.payment_id == payment_id
        )
        .first()
    )

    if not payment:
        return {
            "status": "NOT_FOUND",
            "payment_id": payment_id,
            "reason": "Payment does not exist in local database"
        }

    # 2. Ask Razorpay for independent provider truth
    try:
        provider = RazorpayProvider()

        provider_payment = provider.fetch_payment(
            payment_id
        )

    except RazorpayProviderError as error:

        return {
            "status": "NEEDS_REVIEW",
            "payment_id": payment_id,
            "database_status": payment.status,
            "reason": "Unable to verify payment with Razorpay",
            "provider_error": str(error)
        }

    # 3. Extract provider information
    provider_status = provider_payment.get(
        "internal_status"
    )

    provider_amount_paise = provider_payment.get(
        "amount"
    )

    provider_currency = provider_payment.get(
        "currency"
    )

    # 4. Convert Razorpay amount
    # Razorpay returns amount in paise.
    provider_amount = (
        Decimal(provider_amount_paise) / Decimal("100")
        if provider_amount_paise is not None
        else None
    )

    database_amount = Decimal(
        str(payment.amount)
    )

    # 5. Compare amount
    amount_match = (
        provider_amount == database_amount
    )

    # 6. Compare currency
    currency_match = (
        provider_currency == payment.currency
    )

    # 7. Compare payment status
    status_match = (
        provider_status == payment.status
    )

    # 8. Everything matches
    if (
        status_match
        and amount_match
        and currency_match
    ):
        return {
            "status": "CONSISTENT",
            "payment_id": payment_id,
            "database_status": payment.status,
            "provider_status": provider_status,
            "database_amount": float(database_amount),
            "provider_amount": float(provider_amount),
            "currency": payment.currency
        }

    # 9. Build mismatch reasons
    mismatch_reasons = []

    if not status_match:
        mismatch_reasons.append(
            f"Status mismatch: "
            f"database={payment.status}, "
            f"provider={provider_status}"
        )

    if not amount_match:
        mismatch_reasons.append(
            f"Amount mismatch: "
            f"database={database_amount}, "
            f"provider={provider_amount}"
        )

    if not currency_match:
        mismatch_reasons.append(
            f"Currency mismatch: "
            f"database={payment.currency}, "
            f"provider={provider_currency}"
        )

    return {
        "status": "MISMATCH",
        "payment_id": payment_id,
        "database_status": payment.status,
        "provider_status": provider_status,
        "database_amount": float(database_amount),
        "provider_amount": float(provider_amount),
        "database_currency": payment.currency,
        "provider_currency": provider_currency,
        "reasons": mismatch_reasons
    }