from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.models import CrtProgramBooking, PatientSlotBooking, Payment
from app.services.appointment_service import AppointmentService


def test_structured_segments_create_one_completion_ledger_entry():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    parent = CrtProgramBooking(
        id=40,
        patient_id=12,
        program_id=10,
        slot_date=date(2026, 10, 16),
        total_amount=9000,
        paid_amount=0,
        due_amount=9000,
        is_package_session=False,
    )
    first_segment = PatientSlotBooking(
        id=41,
        patient_id=12,
        therapist_slot_mapping_id=101,
        program_id=10,
        crt_program_booking=parent,
        amount=9000,
        paid_amount=0,
        due_amount=9000,
        is_package_session=False,
    )
    second_segment = PatientSlotBooking(
        id=42,
        patient_id=12,
        therapist_slot_mapping_id=102,
        program_id=10,
        crt_program_booking=parent,
        amount=0,
        paid_amount=0,
        due_amount=0,
        is_package_session=False,
    )
    db.add_all([parent, first_segment, second_segment])
    db.flush()

    AppointmentService._record_slot_payment_if_missing(
        db,
        first_segment,
        amount=0,
        due_amount=9000,
        remark_text="Session completed",
    )
    AppointmentService._record_slot_payment_if_missing(
        db,
        second_segment,
        amount=0,
        due_amount=9000,
        remark_text="Session completed",
    )
    db.flush()

    rows = db.query(Payment).filter(Payment.crt_program_booking_id == parent.id).all()
    assert len(rows) == 1
    assert rows[0].amount == 9000
    assert rows[0].due_amount == 9000

    db.close()
