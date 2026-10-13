"""Correct the September CRT/Vocational demo as partial package sessions."""
from datetime import date, datetime, timedelta

from sqlalchemy import func

from app.core.database import SessionLocal
from app.models.models import CrtProgramBooking, Package, PatientPackage, PatientSlotBooking, Payment
from app.models.payouts import ProgramPaymentConfig, TherapistMonthlyPayout, TherapistPaymentEarning
from app.services.payment_earning_service import sync_payment_earnings

MONTH_START = date(2026, 9, 1)
MONTH_END = date(2026, 9, 30)
CASES = (
    {
        "program_id": 28,
        "patient_id": 46,
        "package_id": 326,
        "transaction_1": "TEST-SEP-ALL-2026-P28-C46-I1",
        "transaction_2": "TEST-SEP-ALL-2026-P28-C46-I2",
        "september_paid": 2000.0,
    },
    {
        "program_id": 34,
        "patient_id": 47,
        "package_id": 327,
        "transaction_1": "TEST-SEP-ALL-2026-P34-C47-I1",
        "transaction_2": "TEST-SEP-ALL-2026-P34-C47-I2",
        "september_paid": 500.0,
    },
)


def patient_package_for_demo(db, case, package):
    row = (
        db.query(PatientPackage)
        .filter(
            PatientPackage.patient_id == case["patient_id"],
            PatientPackage.package_id == package.id,
            PatientPackage.start_date == MONTH_END,
            PatientPackage.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        row = PatientPackage(
            patient_id=case["patient_id"],
            package_id=package.id,
            start_date=MONTH_END,
        )
        db.add(row)
    row.end_date = MONTH_END + timedelta(days=int(package.duration_days or 365))
    row.sessions_completed = 1
    row.sessions_remaining = max(0, int(package.total_sessions or 1) - 1)
    row.total_amount = float(package.price or 0)
    row.paid_amount = case["september_paid"]
    row.due_amount = max(0, row.total_amount - row.paid_amount)
    row.fully_paid = False
    row.payment_status = "PARTIAL"
    row.status = "active"
    db.flush()
    return row


def main():
    db = SessionLocal()
    try:
        # CRT and Vocational follow the same package-percentage payout rule.
        configs = (
            db.query(ProgramPaymentConfig)
            .filter(
                ProgramPaymentConfig.program_id.in_([28, 34]),
                ProgramPaymentConfig.is_active.is_(True),
                ProgramPaymentConfig.deleted_at.is_(None),
            )
            .all()
        )
        for config in configs:
            config.payment_method = "package"
            config.percentage = None
            config.fixed_amount = None
            config.therapist_pool_percentage = 50.0

        summary = []
        affected_payment_ids = set()
        for case in CASES:
            package = db.get(Package, case["package_id"])
            if not package or not package.is_active:
                raise RuntimeError(f"Active package {case['package_id']} is required")
            first = db.query(Payment).filter(Payment.transaction_id == case["transaction_1"]).first()
            second = db.query(Payment).filter(Payment.transaction_id == case["transaction_2"]).first()
            if not first:
                raise RuntimeError(f"Demo payment {case['transaction_1']} was not found")
            booking = db.get(PatientSlotBooking, first.patient_slot_booking_id)
            patient_package = patient_package_for_demo(db, case, package)
            session_amount = round(float(package.price or 0) / max(1, int(package.total_sessions or 1)), 2)
            due = round(session_amount - case["september_paid"], 2)

            if second:
                affected_payment_ids.add(second.id)
                db.query(TherapistPaymentEarning).filter(
                    TherapistPaymentEarning.payment_id == second.id
                ).delete(synchronize_session=False)
                db.delete(second)
            affected_payment_ids.add(first.id)
            first.amount = session_amount
            first.payment_amount = case["september_paid"]
            first.payment_status = "PARTIAL"
            first.payment_date = datetime(2026, 9, 30, 12, 0)
            first.fully_paid = False
            first.due_amount = due
            first.payment_note = "September partial package-session payment; remaining balance to be paid manually in October"

            booking.patient_package_id = patient_package.id
            booking.is_package_session = True
            booking.amount = session_amount
            booking.paid_amount = case["september_paid"]
            booking.package_covered_amount = 0
            booking.due_amount = due
            booking.fully_paid = False
            booking.payment_status = "PARTIAL"
            if booking.crt_program_booking_id:
                crt_parent = db.get(CrtProgramBooking, booking.crt_program_booking_id)
                crt_parent.patient_package_id = patient_package.id
                crt_parent.is_package_session = True
                crt_parent.total_amount = session_amount
                crt_parent.paid_amount = case["september_paid"]
                crt_parent.package_covered_amount = 0
                crt_parent.due_amount = due
                crt_parent.fully_paid = False
                crt_parent.payment_status = "PARTIAL"
                first.crt_program_booking_id = crt_parent.id
            summary.append({
                "programId": case["program_id"],
                "bookingId": booking.id,
                "packageId": patient_package.id,
                "sessionAmount": session_amount,
                "septemberPaid": case["september_paid"],
                "remainingForOctober": due,
            })

        db.flush()
        if affected_payment_ids:
            db.query(TherapistPaymentEarning).filter(
                TherapistPaymentEarning.payment_id.in_(affected_payment_ids)
            ).delete(synchronize_session=False)
        sync_result = sync_payment_earnings(db, MONTH_START, MONTH_END, region_id=1)

        drafts = db.query(TherapistMonthlyPayout).filter(
            TherapistMonthlyPayout.payout_month == MONTH_START,
            TherapistMonthlyPayout.status == "draft",
            TherapistMonthlyPayout.deleted_at.is_(None),
        ).all()
        for draft in drafts:
            gross = float(db.query(func.coalesce(func.sum(TherapistPaymentEarning.therapist_amount), 0)).filter(
                TherapistPaymentEarning.therapist_id == draft.therapist_id,
                TherapistPaymentEarning.earning_date.between(MONTH_START, MONTH_END),
                TherapistPaymentEarning.status == "eligible",
                TherapistPaymentEarning.deleted_at.is_(None),
            ).scalar() or 0)
            draft.gross_amount = round(gross, 2)
            draft.payable_amount = round(gross + float(draft.adjustment_amount or 0), 2)

        db.commit()
        print({"updated": summary, "sync": sync_result})
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()