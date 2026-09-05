from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.models.recovery_job import RecoveryJob
from app.reconciliation.reconciliation import reconcile_payment
from app.database.database import SessionLocal


def process_reconciliation_jobs():
    db: Session = SessionLocal()

    try:
        payments = db.query(Payment).filter(
            Payment.status.in_(["PENDING", "AUTHORIZED"])
        ).all()

        for payment in payments:

            result = reconcile_payment(
                payment.payment_id,
                db
            )

            print(
                f"Reconciliation: "
                f"{payment.payment_id} -> "
                f"{result['status']}"
            )

            # Automatically queue mismatched payments
            if result["status"] == "MISMATCH":

                existing_job = db.query(RecoveryJob).filter(
                    RecoveryJob.payment_id == payment.payment_id
                ).first()

                if not existing_job:

                    recovery_job = RecoveryJob(
                        payment_id=payment.payment_id,
                        status="QUEUED",
                        retry_count=0,
                        max_retries=3
                    )

                    db.add(recovery_job)

                    print(
                        f"Recovery queued: "
                        f"{payment.payment_id}"
                    )

        db.commit()

    except Exception as error:

        db.rollback()

        print(
            "Reconciliation worker error:",
            error
        )

    finally:

        db.close()