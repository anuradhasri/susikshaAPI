"""Payment-date based therapist earning ledger."""
from calendar import monthrange
from datetime import date, datetime, time, timedelta
import json

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from app.models.models import Payment, PatientSlotBooking, Therapist, TherapistSlotMapping
from app.models.payouts import (
    ProgramPaymentConfig, TherapistMonthlyPayout, TherapistPaymentEarning,
)


def money(value):
    return round(float(value or 0) + 1e-9, 2)


def calculate_installment(payment_amount, therapist_count, gst_rate, method, configured_share=0, fixed_amount=None, total_due=None):
    """Calculate one therapist's share of one payment installment."""
    count = max(1, int(therapist_count or 1))
    received = money(float(payment_amount or 0) / count)
    gst = money(received * float(gst_rate or 0) / 100)
    after_gst = money(received - gst)
    if method == "parent":
        therapist_amount = money(after_gst * 0.40 * float(configured_share or 0) / 100)
    elif method == "package":
        therapist_amount = money(after_gst * float(configured_share or 0) / 100)
    elif method == "fixed":
        ratio = min(1.0, float(payment_amount or 0) / max(float(total_due or 0), 1.0))
        therapist_amount = money(float(fixed_amount or 0) * ratio / count)
    else:
        therapist_amount = money(after_gst * float(configured_share or 0) / 100)
    return {"received": received, "gst": gst, "after_gst": after_gst, "therapist": therapist_amount, "organization": money(after_gst - therapist_amount)}

def _config(db, therapist_id, program_id, earning_date):
    return (db.query(ProgramPaymentConfig)
        .filter(
            ProgramPaymentConfig.therapist_id == therapist_id,
            ProgramPaymentConfig.program_id == program_id,
            ProgramPaymentConfig.is_active.is_(True),
            ProgramPaymentConfig.deleted_at.is_(None),
            ProgramPaymentConfig.effective_from <= earning_date,
            or_(ProgramPaymentConfig.effective_to.is_(None), ProgramPaymentConfig.effective_to >= earning_date),
        )
        .order_by(ProgramPaymentConfig.effective_from.desc()).first())


def sync_payment_earnings(db, start_date: date, end_date: date, region_id=None):
    """Upsert one earning per positive payment installment and attending therapist."""
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    query = (db.query(Payment)
        .join(PatientSlotBooking, PatientSlotBooking.id == Payment.patient_slot_booking_id)
        .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
        .filter(
            Payment.payment_amount > 0,
            func.coalesce(Payment.payment_date, Payment.created_at) >= start_dt,
            func.coalesce(Payment.payment_date, Payment.created_at) < end_dt,
            Payment.patient_slot_booking_id.isnot(None),
        ))
    if region_id:
        query = query.join(Therapist, Therapist.id == TherapistSlotMapping.therapist_id).filter(Therapist.region_id == region_id)
    payments = query.order_by(Payment.id).all()
    created = updated = skipped = 0
    for payment in payments:
        booking = db.query(PatientSlotBooking).options(joinedload(PatientSlotBooking.program), joinedload(PatientSlotBooking.therapist_slot_mapping)).get(payment.patient_slot_booking_id)
        if not booking or not booking.program_id or not booking.therapist_slot_mapping:
            skipped += 1; continue
        payment_timestamp = payment.payment_date or payment.created_at
        earning_date = payment_timestamp.date() if isinstance(payment_timestamp, datetime) else payment_timestamp
        if booking.group_program_booking_id:
            group_rows = (db.query(PatientSlotBooking)
                .options(joinedload(PatientSlotBooking.therapist_slot_mapping))
                .filter(PatientSlotBooking.group_program_booking_id == booking.group_program_booking_id).all())
            therapist_ids = sorted({r.therapist_slot_mapping.therapist_id for r in group_rows if r.therapist_slot_mapping})
        else:
            therapist_ids = [booking.therapist_slot_mapping.therapist_id]
        if not therapist_ids:
            skipped += 1; continue
        program_name = str(booking.program.program_name if booking.program else '').lower()
        for therapist_id in therapist_ids:
            config = _config(db, therapist_id, booking.program_id, earning_date)
            if not config:
                skipped += 1; continue
            monthly = (db.query(TherapistMonthlyPayout)
                .filter_by(therapist_id=therapist_id, payout_month=earning_date.replace(day=1)).first())
            if monthly and monthly.status != 'draft':
                skipped += 1; continue
            if 'parent' in program_name:
                configured_share = float(config.therapist_pool_percentage if config.therapist_pool_percentage is not None else 100)
                calculation_method = 'parent'
                method = 'parent_payment_installment'
            elif config.payment_method == 'package':
                configured_share = float(config.therapist_pool_percentage if config.therapist_pool_percentage is not None else 50)
                calculation_method = 'package'
                method = 'package_payment_installment'
            elif config.fixed_amount is not None and config.payment_method in {'fixed','completion'}:
                configured_share = None
                calculation_method = 'fixed'
                method = 'fixed_payment_installment'
            else:
                configured_share = float(config.percentage or 0)
                calculation_method = 'percentage'
                method = 'percentage_payment_installment'
            amounts = calculate_installment(
                payment.payment_amount, len(therapist_ids), config.gst_rate,
                calculation_method, configured_share or 0, config.fixed_amount,
                payment.amount or booking.amount,
            )
            received_share = amounts['received']
            gst = amounts['gst']
            after_gst = amounts['after_gst']
            therapist_amount = amounts['therapist']
            organization_amount = amounts['organization']
            values = dict(
                patient_slot_booking_id=booking.id, program_id=booking.program_id, config_id=config.id,
                earning_date=earning_date, received_amount=received_share, gst_amount=gst,
                amount_after_gst=after_gst, therapist_amount=therapist_amount,
                organization_amount=organization_amount, status='eligible',
                calculation_json=json.dumps({
                    'method': method, 'paymentId': payment.id,
                    'paymentAmount': float(payment.payment_amount or 0),
                    'therapistCount': len(therapist_ids), 'configuredShare': configured_share,
                    'groupProgramBookingId': booking.group_program_booking_id,
                }),
            )
            row = db.query(TherapistPaymentEarning).filter_by(payment_id=payment.id, therapist_id=therapist_id).first()
            if row:
                if row.monthly_payout_id:
                    linked = db.get(TherapistMonthlyPayout, row.monthly_payout_id)
                    if linked and linked.status != 'draft':
                        skipped += 1; continue
                for key, value in values.items(): setattr(row, key, value)
                updated += 1
            else:
                db.add(TherapistPaymentEarning(payment_id=payment.id, therapist_id=therapist_id, **values))
                created += 1
    db.flush()
    return {'created': created, 'updated': updated, 'skipped': skipped}