from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.recovery_job import RecoveryJob
from app.recovery.recovery import recover_payment


def process_recovery_jobs(db: Session):
    """
    Process queued recovery jobs.

    Handles:
    - successful recovery
    - already recovered payments
    - retryable failures
    - permanent failures
    - exponential backoff
    """

    jobs = (
        db.query(RecoveryJob)
        .filter(
            RecoveryJob.status.in_(
                ["QUEUED", "RETRY_WAIT"]
            ),
            (
                (RecoveryJob.next_retry_at == None)
                |
                (
                    RecoveryJob.next_retry_at
                    <= datetime.utcnow()
                )
            )
        )
        .all()
    )

    for job in jobs:

        # --------------------------------------------------
        # Mark job as processing
        # --------------------------------------------------

        job.status = "PROCESSING"
        db.commit()

        try:

            result = recover_payment(
                payment_id=job.payment_id,
                db=db
            )

            result_status = result.get("status")

            # --------------------------------------------------
            # Recovery successful
            # --------------------------------------------------

            if result_status == "RECOVERED":

                job.status = "COMPLETED"
                job.last_error = None
                job.next_retry_at = None

                db.commit()

                print(
                    f"Recovery completed: "
                    f"{job.payment_id}"
                )

            # --------------------------------------------------
            # Payment was already recovered
            # --------------------------------------------------

            elif result_status == "ALREADY_RECOVERED":

                job.status = "COMPLETED"
                job.last_error = None
                job.next_retry_at = None

                db.commit()

                print(
                    f"Recovery skipped; payment already "
                    f"successful: {job.payment_id}"
                )

            # --------------------------------------------------
            # Retry required
            # --------------------------------------------------

            elif result_status == "RETRY_REQUIRED":

                job.retry_count += 1

                if job.retry_count >= job.max_retries:

                    job.status = "FAILED"

                    job.last_error = result.get(
                        "message",
                        "Recovery failed"
                    )

                    job.next_retry_at = None

                    db.commit()

                    print(
                        f"Recovery permanently failed: "
                        f"{job.payment_id}"
                    )

                else:

                    # Exponential backoff:
                    # attempt 1 -> 10 seconds
                    # attempt 2 -> 20 seconds
                    # attempt 3 -> 40 seconds

                    delay_seconds = (
                        10
                        * (
                            2
                            ** (job.retry_count - 1)
                        )
                    )

                    job.status = "RETRY_WAIT"

                    job.next_retry_at = (
                        datetime.utcnow()
                        + timedelta(
                            seconds=delay_seconds
                        )
                    )

                    job.last_error = result.get(
                        "message",
                        "Recovery requires retry"
                    )

                    db.commit()

                    print(
                        f"Recovery retry scheduled: "
                        f"{job.payment_id} "
                        f"in {delay_seconds} seconds"
                    )

            # --------------------------------------------------
            # Recovery failed
            # --------------------------------------------------

            elif result_status == "RECOVERY_FAILED":

                job.retry_count += 1

                if job.retry_count >= job.max_retries:

                    job.status = "FAILED"

                    job.last_error = result.get(
                        "message",
                        result.get(
                            "error",
                            "Recovery failed"
                        )
                    )

                    job.next_retry_at = None

                    db.commit()

                    print(
                        f"Recovery permanently failed: "
                        f"{job.payment_id}"
                    )

                else:

                    delay_seconds = (
                        10
                        * (
                            2
                            ** (job.retry_count - 1)
                        )
                    )

                    job.status = "RETRY_WAIT"

                    job.next_retry_at = (
                        datetime.utcnow()
                        + timedelta(
                            seconds=delay_seconds
                        )
                    )

                    job.last_error = result.get(
                        "message",
                        result.get(
                            "error",
                            "Recovery failed"
                        )
                    )

                    db.commit()

                    print(
                        f"Recovery retry scheduled: "
                        f"{job.payment_id} "
                        f"in {delay_seconds} seconds"
                    )

            # --------------------------------------------------
            # Recovery blocked / not recoverable
            # --------------------------------------------------

            else:

                job.status = "FAILED"

                job.last_error = result.get(
                    "reason",
                    result.get(
                        "message",
                        "Recovery blocked"
                    )
                )

                job.next_retry_at = None

                db.commit()

                print(
                    f"Recovery blocked: "
                    f"{job.payment_id}"
                )

        except Exception as error:

            db.rollback()

            # Re-load job after rollback
            job = (
                db.query(RecoveryJob)
                .filter(
                    RecoveryJob.id == job.id
                )
                .first()
            )

            if not job:
                continue

            job.retry_count += 1

            if job.retry_count >= job.max_retries:

                job.status = "FAILED"

                job.next_retry_at = None

            else:

                delay_seconds = (
                    10
                    * (
                        2
                        ** (job.retry_count - 1)
                    )
                )

                job.status = "RETRY_WAIT"

                job.next_retry_at = (
                    datetime.utcnow()
                    + timedelta(
                        seconds=delay_seconds
                    )
                )

            job.last_error = str(error)

            db.commit()

            print(
                f"Recovery worker exception: "
                f"{job.payment_id}: {error}"
            )