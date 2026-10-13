"""Create duplicate-safe September 2026 split-payment demos for all remaining Mulund programs."""
from datetime import date, datetime

from app.core.database import SessionLocal
from app.models.models import GroupProgramBooking, PatientSlotBooking, Payment, SlotMaster, TherapistSlotMapping

PREFIX = "TEST-SEP-ALL-2026"
SESSION_DATE = date(2026, 9, 30)

PROGRAMS = [
    # program_id, slot_id, amount per child, patient ids, installments per child
    (579, 2, 1800, (45,), (700, 1100)),   # Academic Intervention
    (28, 3, 2000, (46,), (800, 1200)),    # CRT
    (34, 4, 2400, (47,), (1000, 1400)),   # Vocational
    (382, 5, 2500, (45, 46), (1000, 1500)),  # Parent Prewriting
    (385, 6, 1800, (46, 47), (800, 1000)),   # Parent Toilet Training
    (40, 7, 1600, (45,), (600, 1000)),    # Social Group
    (46, 8, 1400, (46,), (400, 1000)),    # Schooling
    (582, 9, 2200, (47,), (900, 1300)),   # Sushiksha Online
]
PARENT_PROGRAMS = {382, 385}


def get_mapping(db, slot_id):
    row = db.query(TherapistSlotMapping).filter_by(
        therapist_id=10, slot_id=slot_id, slot_date=SESSION_DATE
    ).first()
    if row:
        return row, False
    row = TherapistSlotMapping(
        therapist_id=10, slot_id=slot_id, slot_date=SESSION_DATE,
        therapy_id=1, status_id=1,
    )
    db.add(row); db.flush()
    return row, True


def add_installment(db, booking, patient_id, total, paid, index, program_id, hour):
    reference = f"{PREFIX}-P{program_id}-C{patient_id}-I{index}"
    row = db.query(Payment).filter(Payment.transaction_id == reference).first()
    if row:
        return False
    is_final = index == 2
    remaining = 0 if is_final else total - paid
    row = Payment(
        patient_id=patient_id, patient_slot_booking_id=booking.id,
        amount=total, payment_amount=paid,
        payment_status="PAID" if is_final else "PARTIAL",
        payment_date=datetime(2026, 9, 30, hour, index * 10),
        fully_paid=is_final, due_amount=remaining,
        transaction_id=reference,
        payment_note="September all-program payout demo installment",
        remark=f"{PREFIX} program {program_id} booking {booking.id}",
    )
    db.add(row)
    return True


def main():
    db = SessionLocal()
    created = {"mappings": 0, "groups": 0, "bookings": 0, "payments": 0}
    result = []
    try:
        for sequence, (program_id, slot_id, total, patient_ids, installments) in enumerate(PROGRAMS, 1):
            mapping, made_mapping = get_mapping(db, slot_id)
            created["mappings"] += int(made_mapping)
            group = None
            if program_id in PARENT_PROGRAMS:
                first_ref = f"{PREFIX}-P{program_id}-C{patient_ids[0]}-I1"
                existing = db.query(Payment).filter(Payment.transaction_id == first_ref).first()
                if existing:
                    existing_booking = db.get(PatientSlotBooking, existing.patient_slot_booking_id)
                    group = db.get(GroupProgramBooking, existing_booking.group_program_booking_id)
                else:
                    slot = db.get(SlotMaster, slot_id)
                    group = GroupProgramBooking(
                        program_id=program_id, region_id=1, therapy_id=1, slot_id=slot_id,
                        slot_date=SESSION_DATE, start_time=slot.start_time, end_time=slot.end_time,
                        status_id=3,
                    )
                    db.add(group); db.flush(); created["groups"] += 1
            booking_ids = []
            for patient_id in patient_ids:
                first_ref = f"{PREFIX}-P{program_id}-C{patient_id}-I1"
                existing_payment = db.query(Payment).filter(Payment.transaction_id == first_ref).first()
                if existing_payment:
                    booking = db.get(PatientSlotBooking, existing_payment.patient_slot_booking_id)
                else:
                    booking = PatientSlotBooking(
                        patient_id=patient_id, therapist_slot_mapping_id=mapping.id,
                        group_program_booking_id=group.id if group else None,
                        program_id=program_id, duration_minutes=45,
                        amount=total, paid_amount=total, due_amount=0,
                        fully_paid=True, payment_status="PAID", status_id=3,
                    )
                    db.add(booking); db.flush(); created["bookings"] += 1
                booking_ids.append(booking.id)
                for index, paid in enumerate(installments, 1):
                    created["payments"] += int(add_installment(
                        db, booking, patient_id, total, paid, index, program_id, 8 + sequence
                    ))
            result.append({"programId": program_id, "bookingIds": booking_ids, "amount": total * len(patient_ids)})
        db.commit()
        print({"created": created, "programs": result})
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()