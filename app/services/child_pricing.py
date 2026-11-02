"""Resolve child rates at actual completion, preserving earlier same-day sessions."""
from datetime import datetime, time, timezone, timedelta
from sqlalchemy import or_
from app.models.payouts import PatientProgramPricing

INDIA = timezone(timedelta(hours=5, minutes=30))

def pricing_starts_at(pricing):
    start = datetime.combine(pricing.effective_from, time.min)
    if pricing.created_at:
        created = pricing.created_at
        # Pricing.created_at is a database CURRENT_TIMESTAMP in India time.
        local = created if created.tzinfo is None else created.astimezone(INDIA).replace(tzinfo=None)
        if local.date() == pricing.effective_from:
            start = local
    return start

def session_pricing(db, patient_id, program_id, session_date, start_time=None, completed_at=None):
    # UTC timestamps are persisted; session dates and price cutoffs use India time.
    completed_local = None
    if completed_at is not None:
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        completed_local = completed_at.astimezone(INDIA).replace(tzinfo=None)
    prices = db.query(PatientProgramPricing).filter(
        PatientProgramPricing.patient_id == patient_id,
        PatientProgramPricing.program_id == program_id,
        PatientProgramPricing.is_active.is_(True), PatientProgramPricing.deleted_at.is_(None),
        PatientProgramPricing.effective_from <= session_date,
        or_(PatientProgramPricing.effective_to.is_(None), PatientProgramPricing.effective_to >= session_date),
    ).order_by(PatientProgramPricing.effective_from.desc(), PatientProgramPricing.id.desc()).all()
    for price in prices:
        cutoff = pricing_starts_at(price)
        if session_date > cutoff.date():
            return price
        if session_date == cutoff.date():
            if cutoff.time() == time.min:
                return price
            if completed_local is not None and completed_local > cutoff:
                return price
    return None


def booking_completion_time(db, booking):
    if booking.completed_at is not None:
        return booking.completed_at
    # Older app processes did not populate completed_at; use the immutable
    # completion event, never the booking's mutable updated_at timestamp.
    from app.models.models import Payment
    event = db.query(Payment.payment_date, Payment.created_at).filter(
        Payment.patient_id == booking.patient_id,
        or_(Payment.patient_slot_booking_id == booking.id, Payment.remark.like(f'[slot:{booking.id}]%')),
        Payment.remark.like('%Session completed'),
    ).order_by(Payment.id.asc()).first()
    if not event:
        return None
    if event.payment_date:
        return event.payment_date
    if event.created_at:
        created = event.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=INDIA)
        return created.astimezone(timezone.utc).replace(tzinfo=None)
    return None
