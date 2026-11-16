"""Collect ORM changes inside a transaction; publish only after the outer commit."""
import logging
import re
from sqlalchemy import event, inspect, select, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
_installed = set()
KEY = '_live_changes'
TOPICS = {
    'notifications': ('notifications',),
    'payment_alert_rule': ('payment-alerts',),
    'payment_alert_event': ('payment-alerts',),
    'payment_alert_delivery': ('notifications',),
    'appointments': ('appointments',), 'appointment_child_mapping': ('appointments',),
    'appointment_therapist_slot': ('appointments',),
    'patient_slot_booking': ('appointments', 'payouts'),
    'therapist_slot_mapping': ('appointments', 'payouts'),
    'crt_program_bookings': ('appointments', 'payouts'),
    'group_program_bookings': ('appointments', 'payouts'),
    'payments': ('appointments', 'payouts', 'payments'),
    'invoices': ('payments',), 'invoice_items': ('payments',),
    'therapist_monthly_payout': ('payouts',), 'therapist_payout_adjustment': ('payouts',),
    'therapist_session_payout': ('payouts',), 'therapist_payment_earning': ('payouts',),
    'therapist_program_payout': ('payouts',), 'therapist_payroll_config': ('payouts',),
    'program_payment_config': ('payouts',), 'program_sheet_config': ('payouts',),
    'therapy_sheet_assignment': ('payouts',), 'therapy_sheet_submission': ('payouts',),
    'payroll_tax_settings': ('payouts',), 'patient_program_pricing': ('payments', 'payouts'),
    'therapists': ('therapists', 'appointments'), 'therapist_leaves': ('therapists', 'appointments'),
    'therapist_availability': ('therapists', 'appointments'),
    'patients': ('patients', 'appointments'), 'patient_packages': ('patients', 'appointments', 'payments'),
    'patient_session_plan': ('patients', 'appointments'),
    'sessions': ('appointments', 'payouts'), 'session_notes': ('appointments',),
    'patient_session_plan_item': ('patients', 'appointments'),
    'enquiries': ('patients',), 'programs': ('programs', 'appointments'), 'regions': ('regions',),
    'users': ('access',), 'user_roles': ('access',), 'user_region_mapping': ('access',),
}
GLOBAL_TABLES = {'payroll_tax_settings', 'regions'}
SCOPE_FKS = {'patient_id', 'therapist_id', 'program_id', 'invoice_id', 'appointment_id',
             'monthly_payout_id', 'patient_slot_booking_id', 'rule_id', 'event_id',
             'assignment_id', 'patient_package_id', 'patient_session_plan_id', 'session_id'}


def _scopes(db, table, values, depth=0):
    direct = values.get('region_id') or values.get('regionid')
    if direct:
        return {int(direct)}
    if depth >= 4:
        return set()
    regions = set()
    for fk in table.foreign_keys:
        value = values.get(fk.parent.name)
        if fk.parent.name not in SCOPE_FKS or value is None:
            continue
        target = fk.column.table
        key = (target.name, fk.column.name, value)
        cache = db.info.setdefault('_live_scope_cache', {})
        if key not in cache:
            row = db.connection().execute(select(target).where(fk.column == value)).mappings().first()
            cache[key] = _scopes(db, target, dict(row), depth + 1) if row else set()
        regions |= cache[key]
    return regions


def _remember(db, table, values, old_region=None):
    topics = TOPICS.get(table.name)
    if not topics:
        return
    targets = set()
    if table.name in {'notifications', 'payment_alert_delivery'}:
        if values.get('user_id'):
            targets.add(int(values['user_id']))
    elif 'access' in topics:
        user_id = values.get('user_id') or values.get('userid') or (values.get('id') if table.name == 'users' else None)
        if user_id:
            targets.add(int(user_id))
    regions = _scopes(db, table, values) if not targets else set()
    if old_region:
        regions.add(int(old_region))
    global_change = table.name in GLOBAL_TABLES
    if not regions and not targets and not global_change:
        return  # Fail closed when a record's centre cannot be resolved.
    key = (tuple(topics), tuple(sorted(regions)), tuple(sorted(targets)), global_change)
    db.info.setdefault(KEY, set()).add(key)
    if table.name == 'therapy_sheet_assignment' and values.get('therapist_id'):
        fk = next((fk for fk in table.foreign_keys if fk.parent.name == 'therapist_id'), None)
        if fk:
            target = fk.column.table
            user_id = db.connection().execute(select(target.c.user_id).where(fk.column == values['therapist_id'])).scalar()
            if user_id:
                db.info[KEY].add((('notifications',), (), (int(user_id),), False))


def install_realtime_events(hub, metadata=None, session_class=Session):
    if session_class in _installed:
        return
    _installed.add(session_class)

    @event.listens_for(session_class, 'after_flush')
    def collect(db, flush_context):
        try:
            with db.no_autoflush:
                for obj in set(db.new) | set(db.dirty) | set(db.deleted):
                    if obj in db.dirty and not db.is_modified(obj, include_collections=False):
                        continue
                    state = inspect(obj)
                    table = state.mapper.local_table
                    if table.name not in TOPICS:
                        continue
                    values = {column.name: getattr(obj, column.key, None) for column in table.columns}
                    history = state.attrs.region_id.history.deleted if 'region_id' in state.attrs else ()
                    _remember(db, table, values, history[0] if history else None)
        except Exception:
            # Live updates must never roll back a valid business transaction.
            logger.exception('Could not collect live-update metadata')

    @event.listens_for(session_class, 'do_orm_execute', retval=True)
    def collect_bulk(state):
        statement = state.statement
        table = getattr(statement, 'table', None)
        sql = getattr(statement, 'text', '')
        raw = re.match(r'^\s*(UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+[^\w]?([a-zA-Z_][a-zA-Z_0-9]*)', sql, re.I) if sql else None
        if raw and metadata is not None:
            table = metadata.tables.get(raw.group(2))
        if table is None or table.name not in TOPICS or not (state.is_update or state.is_delete or state.is_insert or raw):
            return None
        inserting = state.is_insert or bool(raw and raw.group(1).upper().startswith('INSERT'))
        params = {key: bind.value for key, bind in getattr(statement, '_bindparams', {}).items()}
        if isinstance(state.parameters, dict): params.update(state.parameters)
        try:
            if not inserting:
                if raw:
                    where = re.search(r'\bWHERE\b.*', sql, re.I | re.S)
                    query = text('SELECT * FROM ' + table.name + (' ' + where.group(0) if where else ''))
                    rows = state.session.connection().execute(query, params).mappings()
                else:
                    rows = state.session.connection().execute(select(table).where(*statement._where_criteria)).mappings()
                new_region = None
                for key, value in getattr(statement, '_values', {}).items():
                    if str(key) == 'region_id': new_region = getattr(value, 'value', None)
                if raw:
                    assignment = re.search(r'region_id\s*=\s*:(\w+)', sql, re.I)
                    if assignment: new_region = params.get(assignment.group(1))
                for values in rows:
                    _remember(state.session, table, dict(values), new_region)
        except Exception:
            logger.exception('Could not collect bulk live-update metadata')
        result = state.invoke_statement()
        if inserting:
            try:
                key = getattr(result, 'lastrowid', None)
                if key and 'id' in table.c:
                    row = state.session.connection().execute(select(table).where(table.c.id == key)).mappings().first()
                    if row: _remember(state.session, table, dict(row))
            except Exception:
                logger.exception('Could not collect inserted live-update metadata')
        return result

    @event.listens_for(session_class, 'after_transaction_create')
    def savepoint_started(db, transaction):
        if transaction.nested:
            db.info.setdefault('_live_savepoints', {})[transaction] = set(db.info.get(KEY, set()))

    @event.listens_for(session_class, 'after_transaction_end')
    def savepoint_ended(db, transaction):
        db.info.get('_live_savepoints', {}).pop(transaction, None)

    @event.listens_for(session_class, 'after_commit')
    def committed(db):
        if db.in_nested_transaction():
            return
        changes = db.info.pop(KEY, set())
        db.info.pop('_live_scope_cache', None)
        for topics, regions, targets, global_change in changes:
            hub.publish_threadsafe({'topics': list(topics), 'region_ids': list(regions),
                                    'user_ids': list(targets), 'global': global_change,
                                    'origin': db.info.get('live_origin'), 'actor_id': db.info.get('live_actor_id')})

    @event.listens_for(session_class, 'after_rollback')
    def rolled_back(db):
        nested = db.get_nested_transaction()
        if nested:
            db.info[KEY] = set(db.info.get('_live_savepoints', {}).get(nested, set()))
        else:
            db.info.pop(KEY, None)
        db.info.pop('_live_scope_cache', None)
