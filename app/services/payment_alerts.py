import logging
from collections import defaultdict
from datetime import datetime, timedelta, time, timezone, date
from decimal import Decimal
from uuid import uuid4
from urllib.parse import urlencode
from app.services.payment_report_links import report_token

from sqlalchemy import func, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from app.core.database import SessionLocal
from app.models.models import (Patient, Payment, PatientPackage, PatientSlotBooking, TherapistSlotMapping,
    CrtProgramBooking, User, Role, UserRole, UserRegionMapping, Notification, Region)
from app.models.payouts import PatientProgramPricing
from app.models.payment_alerts import PaymentAlertRule, PaymentAlertEvent, PaymentAlertDelivery, PaymentAlertLease

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
STAFF = {'admin', 'front_office', 'frontoffice', 'front_officer', 'central_head', 'centralhead'}
GLOBAL = {'admin', 'central_head', 'centralhead'}
KINDS = ('cash_summary', 'unpaid_sessions', 'unpaid_plans')


def notification_data(rule, payload, active=True):
    if payload.get('patient_id'):
        url = f"/patients/{payload['patient_id']}?tab=payments"
    elif payload.get('period_start') and payload.get('period_end_exclusive'):
        url = '/reports/payment-receipts?' + urlencode({
            'report': report_token(rule.region_id, payload['period_start'],
                                   date.fromisoformat(payload['period_end_exclusive']) - timedelta(days=1)),
        })
    else:
        url = '/configuration/payment-alerts'
    return {'url': url, 'payment_alert': True, 'resolved': not active}


def defaults(kind, today=None):
    return {'interval_days': 10, 'period_days': 10, 'start_date': (today or datetime.now(IST).date()).isoformat(),
            'session_count': 3, 'after_days': 4, 'include_partial': False,
            'plan_types': ['fixed', 'package'], 'program_ids': []}


def eligible_users(db, region_id):
    users = db.query(User).filter(User.is_active.is_(True), User.deleted_at.is_(None)).all()
    result = []
    for user in users:
        roles = {str(name).lower().replace(' ', '_').replace('-', '_') for (name,) in db.query(Role.name).join(
            UserRole, UserRole.role_id == Role.id).filter(UserRole.user_id == user.id,
            UserRole.deleted_at.is_(None), Role.deleted_at.is_(None)).all()}
        mapped = {item.regionid for item in db.query(UserRegionMapping).filter_by(userid=user.id).all()}
        if (roles & GLOBAL and (not mapped or region_id in mapped)) or (roles & STAFF and region_id in mapped):
            result.append(user)
    return result


def money(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.01'))


def unpaid(due, paid, config):
    return money(due) > 0 and (config.get('include_partial', False) or money(paid) <= 0)


def financial_sessions(db, region_id):
    bookings = db.query(PatientSlotBooking).join(Patient).filter(Patient.region_id == region_id).options(
        joinedload(PatientSlotBooking.patient), joinedload(PatientSlotBooking.therapist_slot_mapping),
        joinedload(PatientSlotBooking.patient_slot_booking_status_master),
        joinedload(PatientSlotBooking.crt_program_booking), joinedload(PatientSlotBooking.program)).all()
    groups = defaultdict(list)
    for booking in bookings:
        code = str(booking.patient_slot_booking_status_master.code if booking.patient_slot_booking_status_master else booking.status).upper()
        if 'CANCEL' in code:
            continue
        key = ('crt', booking.crt_program_booking_id) if booking.crt_program_booking_id else (
            ('group', booking.group_program_booking_id, booking.patient_id) if booking.group_program_booking_id else ('slot', booking.id))
        groups[key].append((booking, code))
    result = []
    for key, items in groups.items():
        row = items[0][0]
        parent = row.crt_program_booking
        mapping = row.therapist_slot_mapping
        if not mapping:
            continue
        source = parent or row
        due = money(0 if source.fully_paid else source.due_amount)
        paid = money(source.paid_amount) + money(source.package_covered_amount)
        if key[0] == 'group':
            due = sum((money(0 if item.fully_paid else item.due_amount) for item, _ in items), Decimal('0.00'))
            paid = sum((money(item.paid_amount) + money(item.package_covered_amount) for item, _ in items), Decimal('0.00'))
        result.append({'key': ':'.join(map(str, key)), 'patient': row.patient,
                       'program_id': row.program_id or (parent.program_id if parent else None),
                       'date': mapping.slot_date, 'completed': all(code == 'COMPLETED' for _, code in items),
                       'is_package': bool(source.is_package_session),
                       'due': due, 'paid': paid})
    return result


def candidates(db, rule, now):
    config = rule.config
    today = now.astimezone(IST).date()
    centre = db.get(Region, rule.region_id).name
    events = {}
    if rule.kind == 'cash_summary':
        anchor = date.fromisoformat(config['start_date'])
        interval = config['interval_days']
        cycles = (today - anchor).days // interval
        if cycles < 1:
            return events
        end = anchor + timedelta(days=cycles * interval)
        start = end - timedelta(days=config['period_days'])
        lower = datetime.combine(start, time.min, IST).astimezone(timezone.utc).replace(tzinfo=None)
        upper = datetime.combine(end, time.min, IST).astimezone(timezone.utc).replace(tzinfo=None)
        received_at = func.coalesce(Payment.payment_date, Payment.created_at)
        values = db.query(Payment.payment_amount).join(Patient).filter(Patient.region_id == rule.region_id,
                    received_at >= lower, received_at < upper).all()
        amount = sum((money(value) for (value,) in values), Decimal('0.00'))
        events[f'period:{end.isoformat()}'] = {
            'title': f'Cash received summary · {centre}',
            'message': f'{start:%d/%m/%Y}–{end - timedelta(days=1):%d/%m/%Y}: ₹{amount:,.2f} received across all payment modes.',
            'payload': {'amount': str(amount), 'period_start': str(start), 'period_end_exclusive': str(end)},
        }
        return events
    sessions = financial_sessions(db, rule.region_id)
    selected_programs = set(config.get('program_ids', []))
    if selected_programs:
        sessions = [row for row in sessions if row['program_id'] in selected_programs]
    if rule.kind == 'unpaid_sessions':
        children = defaultdict(list)
        for row in sessions:
            if row['completed'] and unpaid(row['due'], row['paid'], config):
                children[row['patient'].id].append(row)
        for patient_id, rows in children.items():
            if len(rows) < config['session_count']:
                continue
            child = rows[0]['patient']
            amount = sum((row['due'] for row in rows), Decimal('0.00'))
            events[f'child:{patient_id}'] = {'title': f'Unpaid session alert · {centre}',
                'message': f'{child.first_name} {child.last_name}: {len(rows)} completed unpaid sessions; ₹{amount:,.2f} outstanding on those sessions.',
                'payload': {'patient_id': patient_id, 'count': len(rows), 'amount': str(amount)}}
        return events
    if 'package' in config['plan_types']:
        packages = db.query(PatientPackage).join(Patient).filter(Patient.region_id == rule.region_id,
            PatientPackage.deleted_at.is_(None), func.lower(PatientPackage.status) == 'active').options(
                joinedload(PatientPackage.patient), joinedload(PatientPackage.package)).all()
        for package in packages:
            if selected_programs and package.package.program_id not in selected_programs:
                continue
            if package.fully_paid or (today - package.start_date).days < config['after_days'] or not unpaid(package.due_amount, package.paid_amount, config):
                continue
            child = package.patient
            events[f'package:{package.id}'] = {'title': f'Unpaid package reminder · {centre}',
                'message': f'{child.first_name} {child.last_name}: {package.package.name}, started {package.start_date:%d/%m/%Y}; ₹{money(package.due_amount):,.2f} outstanding.',
                'payload': {'patient_id': child.id, 'plan_type': 'package', 'start_date': str(package.start_date), 'amount': str(money(package.due_amount))}}
    if 'fixed' in config['plan_types']:
        plans = db.query(PatientProgramPricing).join(Patient).filter(Patient.region_id == rule.region_id,
            PatientProgramPricing.deleted_at.is_(None), PatientProgramPricing.is_active.is_(True),
            PatientProgramPricing.billing_type == 'per_session').options(joinedload(PatientProgramPricing.patient), joinedload(PatientProgramPricing.program)).all()
        for plan in plans:
            if selected_programs and plan.program_id not in selected_programs:
                continue
            rows = [row for row in sessions if not row['is_package'] and row['patient'].id == plan.patient_id and row['program_id'] == plan.program_id
                    and row['date'] >= plan.effective_from and (not plan.effective_to or row['date'] <= plan.effective_to)]
            if not rows:
                continue
            start = min(row['date'] for row in rows)
            due = sum((row['due'] for row in rows), Decimal('0.00'))
            paid = sum((row['paid'] for row in rows), Decimal('0.00'))
            if (today - start).days < config['after_days'] or not unpaid(due, paid, config):
                continue
            child = plan.patient
            events[f'fixed:{plan.id}'] = {'title': f'Unpaid fixed-plan reminder · {centre}',
                'message': f'{child.first_name} {child.last_name}: {plan.program.program_name}, first session {start:%d/%m/%Y}; ₹{due:,.2f} outstanding.',
                'payload': {'patient_id': child.id, 'plan_type': 'fixed', 'start_date': str(start), 'amount': str(due)}}
    return events


def reconcile_rule(db, rule, now, preview=False):
    desired = candidates(db, rule, now)
    if preview:
        return list(desired.values())
    allowed = {user.id for user in eligible_users(db, rule.region_id)} & set(rule.recipients)
    existing = {event.event_key: event for event in db.query(PaymentAlertEvent).filter_by(rule_id=rule.id).all()}
    # Cash summaries are historical snapshots; they do not become payment-due resolutions.
    if rule.kind != 'cash_summary':
        for key, event in existing.items():
            if event.active and key not in desired:
                desired[key] = {'title': 'Payment alert resolved',
                    'message': f'{event.message.split(":", 1)[0]}: this alert no longer meets the configured reminder conditions. Check the child’s payments for the current balance.',
                    'payload': {**event.payload, 'resolved': True}}
    for key, content in desired.items():
        event = existing.get(key)
        active = not content['payload'].get('resolved', False)
        changed = not event or event.payload != content['payload'] or event.message != content['message']
        if not event:
            event = PaymentAlertEvent(rule_id=rule.id, event_key=key, active=active, updated_at=now.replace(tzinfo=None), **content)
            db.add(event); db.flush()
        elif changed:
            event.title = content['title']; event.message = content['message']; event.payload = content['payload']
            event.active = active; event.updated_at = now.replace(tzinfo=None)
        deliveries = {item.user_id: item for item in db.query(PaymentAlertDelivery).filter_by(event_id=event.id).all()}
        for user_id in allowed:
            delivery = deliveries.get(user_id)
            notification = db.get(Notification, delivery.notification_id) if delivery else None
            if delivery and (not notification or notification.deleted_at is not None):
                continue
            data = notification_data(rule, content['payload'], active)
            if not notification and active:
                notification = Notification(user_id=user_id, title=content['title'], message=content['message'],
                    notification_type='info' if rule.kind == 'cash_summary' else 'warning', is_read=False, data=data)
                db.add(notification); db.flush()
                db.add(PaymentAlertDelivery(event_id=event.id, user_id=user_id, notification_id=notification.id))
            elif notification and changed:
                notification.title = content['title']; notification.message = content['message']; notification.data = data
                notification.notification_type = 'warning' if active and rule.kind != 'cash_summary' else 'info'
                notification.is_read = False
                # Surface a changed balance/resolution in the bell's newest-first list.
                notification.created_at = now.replace(tzinfo=None)
            elif notification and notification.data != data:
                notification.data = data
    db.commit()
    return len(desired)


def run_payment_alerts():
    db = SessionLocal()
    owner = uuid4().hex
    try:
        if not inspect(db.get_bind()).has_table('payment_alert_rule'):
            return
        now = datetime.now(timezone.utc)
        naive = now.replace(tzinfo=None)
        if db.get(PaymentAlertLease, 'worker') is None:
            db.add(PaymentAlertLease(key='worker', owner='', expires_at=naive - timedelta(seconds=1)))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
        acquired = db.query(PaymentAlertLease).filter(PaymentAlertLease.key == 'worker', PaymentAlertLease.expires_at <= naive).update(
            {'owner': owner, 'expires_at': naive + timedelta(minutes=30)}, synchronize_session=False)
        db.commit()
        if not acquired:
            return
        for rule in db.query(PaymentAlertRule).filter_by(enabled=True).all():
            try:
                reconcile_rule(db, rule, now)
            except Exception:
                db.rollback(); logger.exception('Payment alert rule %s failed', rule.id)
    finally:
        try:
            if inspect(db.get_bind()).has_table('payment_alert_lease'):
                db.query(PaymentAlertLease).filter_by(key='worker', owner=owner).update({'expires_at': datetime.utcnow()}, synchronize_session=False)
                db.commit()
        finally:
            db.close()
