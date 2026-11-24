import logging
from collections import defaultdict
from datetime import datetime, timedelta, time, timezone, date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from urllib.parse import urlencode
from app.services.payment_report_links import report_token

from sqlalchemy import func, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from app.core.database import SessionLocal
from app.models.models import (Patient, Payment, PatientPackage, PatientSlotBooking, TherapistSlotMapping,
    CrtProgramBooking, GroupProgramBooking, User, Role, Therapist, UserRole, UserRegionMapping, Notification, Region)
from app.services.therapist_access import THERAPIST_ROLES
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
    return {'url': url, 'payment_alert': True, 'resolved': bool(payload.get('resolved', False)),
            'reminder_stopped': bool(payload.get('reminder_stopped', False))}


def defaults(kind, today=None):
    return {'interval_days': 10, 'period_days': 10, 'start_date': (today or datetime.now(IST).date()).isoformat(),
            'session_count': 3, 'after_days': 4, 'include_partial': False,
            'plan_types': ['fixed', 'package'], 'program_ids': []}


def eligible_users(db, region_id, *, include_role=False):
    users = db.query(User).filter(User.is_active.is_(True), User.deleted_at.is_(None)).all()
    centre_therapists = {user_id for (user_id,) in db.query(Therapist.user_id).filter(
        Therapist.region_id == region_id, Therapist.is_active.is_(True), Therapist.user_id.is_not(None),
    ).all()}
    result = []
    for user in users:
        roles = {str(name).lower().replace(' ', '_').replace('-', '_') for (name,) in db.query(Role.name).join(
            UserRole, UserRole.role_id == Role.id).filter(UserRole.user_id == user.id,
            UserRole.deleted_at.is_(None), Role.deleted_at.is_(None)).all()}
        mapped = {item.regionid for item in db.query(UserRegionMapping).filter_by(userid=user.id).all()}
        if 'admin' in roles and (not mapped or region_id in mapped):
            group = 'Admin'
        elif roles & {'central_head', 'centralhead'} and (not mapped or region_id in mapped):
            group = 'Central Head'
        elif roles & {'front_office', 'frontoffice', 'front_officer'} and region_id in mapped:
            group = 'Front Office'
        elif roles & THERAPIST_ROLES and (region_id in mapped or (not mapped and user.id in centre_therapists)):
            group = 'Therapist'
        else:
            continue
        result.append((user, group) if include_role else user)
    return result


def money(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.01'))


def unpaid(due, paid, config):
    return money(due) > 0 and (config.get('include_partial', False) or money(paid) <= 0)


def financial_sessions(db, region_id):
    bookings = db.query(PatientSlotBooking).join(Patient).filter(Patient.region_id == region_id).options(
        joinedload(PatientSlotBooking.patient), joinedload(PatientSlotBooking.therapist_slot_mapping),
        joinedload(PatientSlotBooking.patient_slot_booking_status_master),
        joinedload(PatientSlotBooking.crt_program_booking).joinedload(CrtProgramBooking.program),
        joinedload(PatientSlotBooking.group_program_booking).joinedload(GroupProgramBooking.program),
        joinedload(PatientSlotBooking.program)).all()
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
        program_booking = parent or row.group_program_booking
        due = money(0 if source.fully_paid else source.due_amount)
        paid = money(source.paid_amount) + money(source.package_covered_amount)
        if key[0] == 'group':
            due = sum((money(0 if item.fully_paid else item.due_amount) for item, _ in items), Decimal('0.00'))
            paid = sum((money(item.paid_amount) + money(item.package_covered_amount) for item, _ in items), Decimal('0.00'))
        result.append({'key': ':'.join(map(str, key)), 'patient': row.patient,
                       'program_id': row.program_id or (program_booking.program_id if program_booking else None),
                       'program': row.program or (program_booking.program if program_booking else None),
                       'date': mapping.slot_date, 'completed': all(code == 'COMPLETED' for _, code in items),
                       'is_package': bool(source.is_package_session or source.patient_package_id),
                       'package_id': source.patient_package_id,
                       'due': due, 'paid': paid})
    return result


def candidates(db, rule, now, *, preview=False):
    config = rule.config
    today = now.astimezone(IST).date()
    centre = db.get(Region, rule.region_id).name
    events = {}
    if rule.kind == 'cash_summary':
        anchor = date.fromisoformat(config['start_date'])
        interval = config['interval_days']
        cycles = 1 if preview else (today - anchor).days // interval
        if cycles < 1:
            return events
        end = anchor + timedelta(days=cycles * interval)
        start = end - timedelta(days=config['period_days'])
        lower = datetime.combine(start, time.min, IST).astimezone(timezone.utc).replace(tzinfo=None)
        upper = datetime.combine(end, time.min, IST).astimezone(timezone.utc).replace(tzinfo=None)
        if preview:
            upper = min(upper, now.astimezone(timezone.utc).replace(tzinfo=None))
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
            if selected_programs and not (set(selected_programs) & ({package.package.program_id} | {a.program_id for a in package.package.program_assignments})):
                continue
            if package.fully_paid or (today - package.start_date).days < config['after_days'] or not unpaid(package.due_amount, package.paid_amount, config):
                continue
            child = package.patient
            events[f'package:{package.id}'] = {'title': f'Unpaid package reminder · {centre}',
                'message': f'{child.first_name} {child.last_name}: {package.package.name}, started {package.start_date:%d/%m/%Y}; ₹{money(package.due_amount):,.2f} outstanding.',
                'payload': {'patient_id': child.id, 'plan_type': 'package', 'start_date': str(package.start_date), 'amount': str(money(package.due_amount))}}
        for row in sessions:
            if not (row['is_package'] and row['completed'] and unpaid(row['due'], row['paid'], config)):
                continue
            if (today - row['date']).days < config['after_days'] or f"package:{row['package_id']}" in events:
                continue
            child = row['patient']
            program_name = row['program'].program_name if row['program'] else 'Session'
            events[f"package-session:{row['key']}"] = {
                'title': f'Unpaid package session reminder — {centre}',
                'message': f"{child.first_name} {child.last_name}: {program_name}, session {row['date']:%d/%m/%Y}; ₹{row['due']:,.2f} outstanding.",
                'payload': {'patient_id': child.id, 'plan_type': 'package',
                            'start_date': str(row['date']), 'amount': str(row['due'])},
            }
    if 'fixed' in config['plan_types']:
        scheduled = defaultdict(list)
        for row in sessions:
            if row['completed'] and not row['is_package'] and row['program_id'] is not None:
                scheduled[row['patient'].id, row['program_id']].append(row)
        covered = set()
        fixed_groups = []
        plans = db.query(PatientProgramPricing).join(Patient).filter(Patient.region_id == rule.region_id,
            PatientProgramPricing.deleted_at.is_(None), PatientProgramPricing.is_active.is_(True),
            PatientProgramPricing.billing_type == 'per_session').options(joinedload(PatientProgramPricing.patient), joinedload(PatientProgramPricing.program)).all()
        for plan in plans:
            if selected_programs and plan.program_id not in selected_programs:
                continue
            rows = [row for row in scheduled[plan.patient_id, plan.program_id]
                    if row['date'] >= plan.effective_from and (not plan.effective_to or row['date'] <= plan.effective_to)]
            if not rows:
                continue
            covered.update(row['key'] for row in rows)
            fixed_groups.append((f'fixed:{plan.id}', plan.patient, plan.program.program_name, rows))
        for (patient_id, program_id), rows in scheduled.items():
            rows = [row for row in rows if row['key'] not in covered]
            if rows:
                program = rows[0]['program']
                fixed_groups.append((f'fixed-bookings:{patient_id}:{program_id}', rows[0]['patient'],
                                     program.program_name if program else 'Program', rows))
        for key, child, program_name, rows in fixed_groups:
            start = min(row['date'] for row in rows)
            due = sum((row['due'] for row in rows), Decimal('0.00'))
            paid = sum((row['paid'] for row in rows), Decimal('0.00'))
            if (today - start).days < config['after_days'] or not unpaid(due, paid, config):
                continue
            payload = {'patient_id': child.id, 'plan_type': 'fixed', 'start_date': str(start), 'amount': str(due)}
            if key.startswith('fixed-bookings:'):
                payload['session_keys'] = sorted(row['key'] for row in rows)
            events[key] = {'title': f'Unpaid fixed-plan reminder · {centre}',
                'message': f'{child.first_name} {child.last_name}: {program_name}, first session {start:%d/%m/%Y}; ₹{due:,.2f} outstanding.',
                'payload': payload}
    return events


def group_plan_reminders(events, centre):
    by_child = defaultdict(dict)
    for key, item in events.items():
        by_child[item['payload']['patient_id']][key] = item
    grouped = {}
    for patient_id, items in by_child.items():
        first = next(iter(items.values()))
        child = first['message'].split(':', 1)[0]
        sections = []
        for label, purchases in (('Package balances', True), ('Session dues', False)):
            lines = [item['message'].split(': ', 1)[-1] for key, item in sorted(items.items())
                     if key.startswith('package:') == purchases]
            if lines:
                sections.append({'label': label, 'lines': lines})
        grouped[f'plan-child:{patient_id}'] = {
            'title': f'Unpaid plan reminder · {centre}',
            'message': child + ':\n\n' + '\n\n'.join(
                section['label'] + '\n' + '\n'.join(f'• {line}' for line in section['lines']) for section in sections),
            'payload': {'patient_id': patient_id, 'child_name': child, 'sections': sections, 'items': items},
        }
    return grouped


def stopped_reminder(db, rule, event):
    # Read balances independently of reminder filters. Leaving a rule is not proof of payment.
    kind, _, identifier = event.event_key.partition(':')
    balance = None
    if kind == 'plan-child':
        results = [stopped_reminder(db, rule, SimpleNamespace(event_key=key, **item))
                   for key, item in event.payload['items'].items()]
        if all('amount' in item['payload'] for item in results):
            balance = sum((money(item['payload']['amount']) for item in results), Decimal('0.00'))
    elif identifier.isdigit() and kind == 'package':
        package = db.get(PatientPackage, int(identifier))
        if package and package.deleted_at is None:
            balance = money(0 if package.fully_paid else package.due_amount)
    elif kind in ('fixed-bookings', 'package-session'):
        keys = {identifier} if kind == 'package-session' else set(event.payload.get('session_keys', []))
        rows = [row for row in financial_sessions(db, rule.region_id) if row['key'] in keys]
        if keys and len(rows) == len(keys):
            balance = sum((max(row['due'], Decimal('0.00')) for row in rows), Decimal('0.00'))
    elif identifier.isdigit() and kind in ('child', 'fixed'):
        rows = financial_sessions(db, rule.region_id)
        if kind == 'child':
            rows = [row for row in rows if row['patient'].id == int(identifier) and row['completed']]
        else:
            plan = db.get(PatientProgramPricing, int(identifier))
            rows = [row for row in rows if plan and plan.deleted_at is None
                    and row['completed'] and not row['is_package'] and row['patient'].id == plan.patient_id
                    and row['program_id'] == plan.program_id and row['date'] >= plan.effective_from
                    and (not plan.effective_to or row['date'] <= plan.effective_to)]
        if rows:
            balance = sum((max(row['due'], Decimal('0.00')) for row in rows), Decimal('0.00'))
    cleared = balance is not None and balance <= 0
    child = event.message.split(':', 1)[0]
    payload = {**event.payload, 'resolved': cleared, 'reminder_stopped': not cleared}
    # Never present the old alert amount as a current balance.
    payload.pop('amount', None)
    if balance is not None:
        payload['amount'] = str(max(balance, Decimal('0.00')))
    if cleared:
        return {'title': 'Payment completed',
                'message': f'{child}: the outstanding balance covered by this reminder is fully cleared.',
                'payload': payload}
    detail = (f'₹{balance:,.2f} remains outstanding.' if balance is not None
              else 'The current balance could not be confirmed. Check the child’s payments.')
    return {'title': 'Reminder stopped',
            'message': f'{child}: this reminder no longer matches the configured conditions. '
                       f'{detail} This does not confirm full payment.', 'payload': payload}


def reconcile_rule(db, rule, now, preview=False):
    desired = candidates(db, rule, now, preview=preview)
    if rule.kind == 'unpaid_plans':
        desired = group_plan_reminders(desired, db.get(Region, rule.region_id).name)
    if preview:
        return list(desired.values())
    allowed = {user.id for user in eligible_users(db, rule.region_id)} & set(rule.recipients)
    existing = {event.event_key: event for event in db.query(PaymentAlertEvent).filter_by(rule_id=rule.id).all()}
    # Cash summaries are historical snapshots; they do not become payment-due resolutions.
    if rule.kind != 'cash_summary':
        for key, event in existing.items():
            if rule.kind == 'unpaid_plans' and not key.startswith('plan-child:') and f"plan-child:{event.payload.get('patient_id')}" in desired:
                event.active = False
                for delivery in db.query(PaymentAlertDelivery).filter_by(event_id=event.id):
                    notice = db.get(Notification, delivery.notification_id)
                    if notice and notice.deleted_at is None:
                        notice.deleted_at = now.replace(tzinfo=None)
                continue
            if (event.active or event.payload.get('reminder_stopped')) and key not in desired:
                desired[key] = stopped_reminder(db, rule, event)
    for key, content in desired.items():
        event = existing.get(key)
        active = not (content['payload'].get('resolved', False) or content['payload'].get('reminder_stopped', False))
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
