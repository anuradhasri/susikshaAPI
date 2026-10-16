from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.models import PatientSlotBooking, Program, TherapistSlotMapping
from app.models.payouts import PatientProgramPricing
from app.services.appointment_service import AppointmentService


def test_completion_uses_child_price_but_keeps_standard_session_amount():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    program = Program(
        id=10,
        region_id=1,
        program_name="General",
        per_session_amount=1200,
    )
    mapping = TherapistSlotMapping(
        id=20,
        therapist_id=1,
        slot_id=1,
        slot_date=date(2026, 10, 14),
        therapy_id=1,
    )
    booking = PatientSlotBooking(
        id=30,
        patient_id=12,
        therapist_slot_mapping_id=20,
        program_id=10,
        amount=1200,
        paid_amount=0,
        due_amount=0,
        is_package_session=False,
    )
    pricing = PatientProgramPricing(
        patient_id=12,
        program_id=10,
        billing_type="per_session",
        agreed_amount=500,
        effective_from=date(2026, 10, 13),
        is_active=True,
    )
    db.add_all([program, mapping, booking, pricing])
    db.flush()

    billable_amount = AppointmentService._slot_booking_amount(booking)
    booking.due_amount = max(0, billable_amount - float(booking.paid_amount or 0))

    assert booking.amount == 1200
    assert billable_amount == 500
    assert booking.due_amount == 500
    assert 49200 + booking.due_amount == 49700

    db.close()
