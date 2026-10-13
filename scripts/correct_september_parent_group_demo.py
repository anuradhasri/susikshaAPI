"""Normalize the PEG demo into one five-session September parent-program group."""
from datetime import date, datetime

from sqlalchemy import func, text

from app.core.database import SessionLocal
from app.models.models import (
    GroupProgramBooking, PatientSlotBooking, Payment, TherapistSlotMapping, User, Therapist,
)
from app.models.payouts import (
    ProgramPaymentConfig, TherapistMonthlyPayout, TherapistPaymentEarning, TherapistSessionAllocation, TherapistSessionPayout,
    TherapySheetAssignment, TherapySheetSubmission,
)
from app.api.routes.payouts import applicable_payroll_config, sync

GROUP_ID = 3
PROGRAM_ID = 388
REGION_ID = 1
PATIENT_IDS = (46, 105, 106)
THERAPIST_IDS = (10, 20)
SESSION_DATES = (
    date(2026, 9, 1),
    date(2026, 9, 8),
    date(2026, 9, 15),
    date(2026, 9, 22),
    date(2026, 9, 29),
)
SESSION_GROUP_IDS = (58, 61, 62, 63, 64)
PER_CHILD_SESSION_AMOUNT = 4000.0


def remove_superseded_seed_group(db):
    seeded_payments = db.query(Payment).filter(
        Payment.transaction_id.like("TEST-SEP-2026-PARENT-%")
    ).all()
    booking_ids = {row.patient_slot_booking_id for row in seeded_payments if row.patient_slot_booking_id}
    payment_ids = {row.id for row in seeded_payments}
    if payment_ids:
        db.query(TherapistPaymentEarning).filter(
            TherapistPaymentEarning.payment_id.in_(payment_ids)
        ).delete(synchronize_session=False)
    if booking_ids:
        assignment_ids = {
            value[0] for value in db.query(TherapySheetAssignment.id).filter(
                TherapySheetAssignment.patient_slot_booking_id.in_(booking_ids)
            ).all()
        }
        if assignment_ids:
            db.query(TherapySheetSubmission).filter(
                TherapySheetSubmission.assignment_id.in_(assignment_ids)
            ).delete(synchronize_session=False)
        db.query(TherapySheetAssignment).filter(
            TherapySheetAssignment.patient_slot_booking_id.in_(booking_ids)
        ).delete(synchronize_session=False)
        db.query(TherapistSessionAllocation).filter(
            TherapistSessionAllocation.patient_slot_booking_id.in_(booking_ids)
        ).delete(synchronize_session=False)
        db.query(TherapistSessionPayout).filter(
            TherapistSessionPayout.patient_slot_booking_id.in_(booking_ids)
        ).delete(synchronize_session=False)
        db.query(Payment).filter(Payment.id.in_(payment_ids)).delete(synchronize_session=False)
        group_ids = {
            value[0] for value in db.query(PatientSlotBooking.group_program_booking_id).filter(
                PatientSlotBooking.id.in_(booking_ids)
            ).all() if value[0]
        }
        db.query(PatientSlotBooking).filter(
            PatientSlotBooking.id.in_(booking_ids)
        ).delete(synchronize_session=False)
        if group_ids:
            db.query(GroupProgramBooking).filter(
                GroupProgramBooking.id.in_(group_ids)
            ).delete(synchronize_session=False)


def mapping_for(db, therapist_id, group_booking, session_date):
    mapping = db.query(TherapistSlotMapping).filter_by(
        therapist_id=therapist_id,
        slot_id=group_booking.slot_id,
        slot_date=session_date,
    ).first()
    if not mapping:
        mapping = TherapistSlotMapping(
            therapist_id=therapist_id,
            slot_id=group_booking.slot_id,
            slot_date=session_date,
            therapy_id=group_booking.therapy_id or 1,
            status_id=1,
        )
        db.add(mapping)
        db.flush()
    return mapping


def normalize_child_rows(db, group_booking, session_date):
    mappings = {
        therapist_id: mapping_for(db, therapist_id, group_booking, session_date)
        for therapist_id in THERAPIST_IDS
    }
    rows = db.query(PatientSlotBooking).filter(
        PatientSlotBooking.group_program_booking_id == group_booking.id
    ).all()
    by_patient_therapist = {}
    for row in rows:
        therapist_id = row.therapist_slot_mapping.therapist_id
        by_patient_therapist[(row.patient_id, therapist_id)] = row

    for patient_id in PATIENT_IDS:
        for therapist_id in THERAPIST_IDS:
            row = by_patient_therapist.get((patient_id, therapist_id))
            if not row:
                row = PatientSlotBooking(
                    patient_id=patient_id,
                    therapist_slot_mapping_id=mappings[therapist_id].id,
                    group_program_booking_id=group_booking.id,
                    program_id=PROGRAM_ID,
                    duration_minutes=45,
                    status_id=3,
                )
                db.add(row)
                db.flush()
                by_patient_therapist[(patient_id, therapist_id)] = row
            else:
                row.therapist_slot_mapping_id = mappings[therapist_id].id
            row.status_id = 3
            row.duration_minutes = 45

        child_rows = [by_patient_therapist[(patient_id, tid)] for tid in THERAPIST_IDS]
        payments = db.query(Payment).filter(
            Payment.patient_slot_booking_id.in_([row.id for row in child_rows])
        ).order_by(func.coalesce(Payment.payment_date, Payment.created_at), Payment.id).all()
        cumulative = 0.0
        for payment in payments:
            cumulative += float(payment.payment_amount or 0)
            payment.amount = PER_CHILD_SESSION_AMOUNT
            payment.due_amount = max(0.0, PER_CHILD_SESSION_AMOUNT - cumulative)
            payment.fully_paid = cumulative >= PER_CHILD_SESSION_AMOUNT
            payment.payment_status = "PAID" if payment.fully_paid else "PARTIAL"

        received = min(PER_CHILD_SESSION_AMOUNT, cumulative)
        payer = by_patient_therapist[(patient_id, 10)]
        payer.amount = PER_CHILD_SESSION_AMOUNT
        payer.paid_amount = received
        payer.package_covered_amount = 0
        payer.due_amount = max(0.0, PER_CHILD_SESSION_AMOUNT - received)
        payer.fully_paid = payer.due_amount <= 0
        payer.payment_status = "PAID" if payer.fully_paid else ("PARTIAL" if received > 0 else "UNPAID")
        secondary = by_patient_therapist[(patient_id, 20)]
        secondary.amount = 0
        secondary.paid_amount = 0
        secondary.package_covered_amount = 0
        secondary.due_amount = 0
        secondary.fully_paid = payer.fully_paid
        secondary.payment_status = payer.payment_status


def set_september_payment_examples(db):
    """One full and one partial PEG payment received during September."""
    examples = (
        (46, 4000.0, datetime(2026, 9, 30, 10, 0), "FULL"),
        (105, 2000.0, datetime(2026, 9, 30, 11, 0), "PARTIAL"),
    )
    for patient_id, paid, paid_at, label in examples:
        booking = (
            db.query(PatientSlotBooking)
            .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
            .filter(
                PatientSlotBooking.group_program_booking_id == 58,
                PatientSlotBooking.patient_id == patient_id,
                TherapistSlotMapping.therapist_id == 10,
            )
            .first()
        )
        if not booking:
            raise RuntimeError(f"September PEG booking for patient {patient_id} was not found")
        payment = (
            db.query(Payment)
            .filter(
                Payment.patient_slot_booking_id == booking.id,
                Payment.payment_amount > 0,
            )
            .order_by(Payment.id.desc())
            .first()
        )
        if not payment:
            raise RuntimeError(f"September PEG payment for patient {patient_id} was not found")
        due = max(0.0, PER_CHILD_SESSION_AMOUNT - paid)
        payment.amount = PER_CHILD_SESSION_AMOUNT
        payment.payment_amount = paid
        payment.payment_date = paid_at
        payment.due_amount = due
        payment.fully_paid = due <= 0
        payment.payment_status = "PAID" if payment.fully_paid else "PARTIAL"
        payment.payment_note = f"September PEG demo {label.lower()} payment"
        # This demo intentionally moves an existing payment across months.
        # Recreate its ledger rows so no stale October draft link can preserve
        # the former earning date.
        db.query(TherapistPaymentEarning).filter(
            TherapistPaymentEarning.payment_id == payment.id
        ).delete(synchronize_session=False)
        booking.amount = PER_CHILD_SESSION_AMOUNT
        booking.paid_amount = paid
        booking.due_amount = due
        booking.fully_paid = due <= 0
        booking.payment_status = "PAID" if booking.fully_paid else "PARTIAL"

def main():
    db = SessionLocal()
    try:
        group = db.execute(text(
            "SELECT id FROM parent_program_groups WHERE id=:id FOR UPDATE"
        ), {"id": GROUP_ID}).first()
        if not group:
            raise RuntimeError("Expected September PEG parent group was not found")

        remove_superseded_seed_group(db)
        denissia_config = db.get(ProgramPaymentConfig, 17)
        if denissia_config and denissia_config.program_id == PROGRAM_ID:
            denissia_config.effective_from = date(2026, 9, 30)

        # Remove two empty placeholder group-booking rows so progress is exactly 5/5.
        for empty_id in (59, 60):
            has_children = db.query(PatientSlotBooking.id).filter(
                PatientSlotBooking.group_program_booking_id == empty_id
            ).first()
            if not has_children:
                db.query(GroupProgramBooking).filter(
                    GroupProgramBooking.id == empty_id
                ).delete(synchronize_session=False)

        db.execute(text("""
            UPDATE parent_program_groups
            SET name=:name, start_date=:start_date, end_date=:end_date,
                pro_rata_amount=20000, number_of_sessions=5,
                status='COMPLETED', completed_at=:completed_at,
                is_active=1, updated_at=CURRENT_TIMESTAMP
            WHERE id=:id
        """), {
            "id": GROUP_ID,
            "name": "Parent Training Program: PEG - September 2026 (5 Sessions)",
            "start_date": SESSION_DATES[0],
            "end_date": date(2026, 9, 30),
            "completed_at": datetime(2026, 9, 29, 18, 0),
        })
        db.execute(text("DELETE FROM parent_program_group_members WHERE group_id=:id"), {"id": GROUP_ID})
        for patient_id in PATIENT_IDS:
            db.execute(text(
                "INSERT INTO parent_program_group_members (group_id,patient_id) VALUES (:group_id,:patient_id)"
            ), {"group_id": GROUP_ID, "patient_id": patient_id})

        for group_booking_id, session_date in zip(SESSION_GROUP_IDS, SESSION_DATES):
            group_booking = db.get(GroupProgramBooking, group_booking_id)
            if not group_booking:
                raise RuntimeError(f"Expected group booking {group_booking_id} was not found")
            group_booking.parent_program_group_id = GROUP_ID
            group_booking.program_id = PROGRAM_ID
            group_booking.region_id = REGION_ID
            group_booking.slot_date = session_date
            group_booking.status_id = 3
            normalize_child_rows(db, group_booking, session_date)

        set_september_payment_examples(db)
        db.commit()
        manager = db.query(User).outerjoin(Therapist, Therapist.user_id == User.id).filter(
            Therapist.id.is_(None)
        ).first()
        result = sync(
            start_date=SESSION_DATES[0],
            end_date=date(2026, 9, 30),
            region_id=REGION_ID,
            db=db,
            user=manager,
        )
        for month_start in (date(2026, 9, 1), date(2026, 10, 1)):
            for therapist_id in THERAPIST_IDS:
                existing = db.query(TherapistMonthlyPayout).filter_by(
                    therapist_id=therapist_id, payout_month=month_start
                ).first()
                ledger_total = float(db.query(func.coalesce(func.sum(TherapistPaymentEarning.therapist_amount), 0)).filter(
                    TherapistPaymentEarning.therapist_id == therapist_id,
                    TherapistPaymentEarning.earning_date >= month_start,
                    TherapistPaymentEarning.earning_date < (date(2026, 10, 1) if month_start.month == 9 else date(2026, 11, 1)),
                    TherapistPaymentEarning.status == "eligible",
                    TherapistPaymentEarning.deleted_at.is_(None),
                ).scalar() or 0)
                if not existing and ledger_total > 0:
                    db.add(TherapistMonthlyPayout(
                        therapist_id=therapist_id,
                        payout_month=month_start,
                        gross_amount=0,
                        adjustment_amount=0,
                        payable_amount=0,
                        status="draft",
                    ))
            db.flush()
        drafts = db.query(TherapistMonthlyPayout).filter(
            TherapistMonthlyPayout.payout_month.in_([date(2026, 9, 1), date(2026, 10, 1)]),
            TherapistMonthlyPayout.therapist_id.in_(THERAPIST_IDS),
            TherapistMonthlyPayout.status == "draft",
            TherapistMonthlyPayout.deleted_at.is_(None),
        ).all()
        for draft in drafts:
            month_start = draft.payout_month
            month_end = date(month_start.year, month_start.month, 30 if month_start.month == 9 else 31)
            session_earnings = float(db.query(func.coalesce(func.sum(TherapistPaymentEarning.therapist_amount), 0)).filter(
                TherapistPaymentEarning.therapist_id == draft.therapist_id,
                TherapistPaymentEarning.earning_date.between(month_start, month_end),
                TherapistPaymentEarning.status == "eligible",
                TherapistPaymentEarning.deleted_at.is_(None),
            ).scalar() or 0)
            payroll = applicable_payroll_config(db, draft.therapist_id, month_end)
            gross = (
                session_earnings
                + float(payroll.base_salary if payroll else 0)
                + float(payroll.flat_commission if payroll else 0)
                - float(payroll.professional_tax if payroll else 0)
            )
            draft.gross_amount = round(gross, 2)
            draft.payable_amount = round(gross + float(draft.adjustment_amount or 0), 2)
        db.commit()
        print({
            "groupId": GROUP_ID,
            "groupAmountPerChild": 20000.0,
            "sessions": 5,
            "perChildSessionAmount": PER_CHILD_SESSION_AMOUNT,
            "members": len(PATIENT_IDS),
            "sync": result["data"],
        })
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()