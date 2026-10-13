"""Create idempotent September 2026 payout demo data."""
from datetime import date, datetime, time

from app.core.database import SessionLocal
from app.models.models import GroupProgramBooking, PatientSlotBooking, Payment, TherapistSlotMapping

PREFIX = "TEST-SEP-2026"


def add_payment(db, booking, patient_id, amount, paid, paid_at, suffix):
    transaction_id = f"{PREFIX}-{suffix}"
    existing = db.query(Payment).filter(Payment.transaction_id == transaction_id).first()
    if existing:
        return existing, False
    row = Payment(
        patient_id=patient_id,
        patient_slot_booking_id=booking.id,
        amount=amount,
        payment_amount=paid,
        payment_status="PAID" if paid >= amount else "PARTIAL",
        payment_date=paid_at,
        fully_paid=False,
        due_amount=max(0, amount - paid),
        transaction_id=transaction_id,
        payment_note="September payout demo installment",
        remark=f"{PREFIX} payment for booking {booking.id}",
    )
    db.add(row)
    return row, True


def main():
    db = SessionLocal()
    created = {"mappings": 0, "groups": 0, "bookings": 0, "payments": 0}
    try:
        normal_payment = db.query(Payment).filter(Payment.transaction_id == f"{PREFIX}-NORMAL-1").first()
        if normal_payment:
            normal_booking = db.get(PatientSlotBooking, normal_payment.patient_slot_booking_id)
        else:
            mapping = TherapistSlotMapping(
                therapist_id=10, slot_id=1, slot_date=date(2026, 9, 29),
                therapy_id=1, status_id=1,
            )
            db.add(mapping); db.flush(); created["mappings"] += 1
            normal_booking = PatientSlotBooking(
                patient_id=45, therapist_slot_mapping_id=mapping.id, program_id=1,
                duration_minutes=45, amount=1200, paid_amount=1200, due_amount=0,
                fully_paid=True, payment_status="PAID", status_id=3,
            )
            db.add(normal_booking); db.flush(); created["bookings"] += 1
        for paid, paid_at, suffix in [
            (500, datetime(2026, 9, 29, 10, 0), "NORMAL-1"),
            (700, datetime(2026, 9, 30, 10, 0), "NORMAL-2"),
        ]:
            _, was_created = add_payment(db, normal_booking, 45, 1200, paid, paid_at, suffix)
            created["payments"] += int(was_created)

        parent_payment = db.query(Payment).filter(Payment.transaction_id == f"{PREFIX}-PARENT-45-1").first()
        if parent_payment:
            parent_booking = db.get(PatientSlotBooking, parent_payment.patient_slot_booking_id)
            group = db.get(GroupProgramBooking, parent_booking.group_program_booking_id)
        else:
            group = GroupProgramBooking(
                program_id=388, region_id=1, therapy_id=1, slot_id=1,
                slot_date=date(2026, 9, 30), start_time=time(11, 0), end_time=time(11, 45), status_id=3,
            )
            db.add(group); db.flush(); created["groups"] += 1
        parent_mapping = db.query(TherapistSlotMapping).filter_by(
            therapist_id=10, slot_id=1, slot_date=date(2026, 9, 30)
        ).first()
        if not parent_mapping:
            parent_mapping = TherapistSlotMapping(
                therapist_id=10, slot_id=1, slot_date=date(2026, 9, 30),
                therapy_id=1, status_id=1,
            )
            db.add(parent_mapping); db.flush(); created["mappings"] += 1
        parent_bookings = {}
        for patient_id in (45, 46):
            existing_payment = db.query(Payment).filter(Payment.transaction_id == f"{PREFIX}-PARENT-{patient_id}-1").first()
            if existing_payment:
                booking = db.get(PatientSlotBooking, existing_payment.patient_slot_booking_id)
            else:
                booking = PatientSlotBooking(
                    patient_id=patient_id, therapist_slot_mapping_id=parent_mapping.id,
                    group_program_booking_id=group.id, program_id=388,
                    duration_minutes=45, amount=3000, paid_amount=3000, due_amount=0,
                    fully_paid=True, payment_status="PAID", status_id=3,
                )
                db.add(booking); db.flush(); created["bookings"] += 1
            parent_bookings[patient_id] = booking
        installments = {
            45: [(1000, datetime(2026, 9, 30, 11, 0)), (2000, datetime(2026, 9, 30, 12, 0))],
            46: [(1500, datetime(2026, 9, 30, 11, 15)), (1500, datetime(2026, 9, 30, 12, 15))],
        }
        for patient_id, values in installments.items():
            for index, (paid, paid_at) in enumerate(values, 1):
                _, was_created = add_payment(db, parent_bookings[patient_id], patient_id, 3000, paid, paid_at, f"PARENT-{patient_id}-{index}")
                created["payments"] += int(was_created)
        db.commit()
        print({"created": created, "normalBookingId": normal_booking.id, "parentGroupId": group.id, "parentBookingIds": [x.id for x in parent_bookings.values()]})
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()