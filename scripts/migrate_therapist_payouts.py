"""Run from autism-backend: python -m scripts.migrate_therapist_payouts"""
from sqlalchemy import inspect, text
from app.core.database import Base, engine
from app.models import models, report_sheets, payouts  # noqa: F401 - register metadata


TABLES = [
    'program_payment_config', 'program_payment_config_history',
    'program_sheet_config',
    'therapy_sheet_assignment', 'therapy_sheet_submission',
    'therapist_session_allocation', 'therapist_monthly_payout',
    'therapist_session_payout', 'therapist_program_payout',
    'therapist_payout_adjustment',
    'therapist_payroll_config',
]


if __name__ == '__main__':
    engine.echo = False
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in TABLES], checkfirst=True)
    inspector = inspect(engine)
    columns = {column['name'] for column in inspector.get_columns('program_payment_config')}
    if 'therapist_id' not in columns:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE program_payment_config ADD COLUMN therapist_id INT NULL AFTER id'))
            indexes = {index['name'] for index in inspect(connection).get_indexes('program_payment_config')}
            if 'uq_program_payment_effective_date' in indexes:
                connection.execute(text('ALTER TABLE program_payment_config DROP INDEX uq_program_payment_effective_date'))
            if 'idx_program_payment_lookup' in indexes:
                connection.execute(text('ALTER TABLE program_payment_config DROP INDEX idx_program_payment_lookup'))
            connection.execute(text('ALTER TABLE program_payment_config ADD UNIQUE KEY uq_therapist_program_payment_effective_date (therapist_id, program_id, effective_from)'))
            connection.execute(text('ALTER TABLE program_payment_config ADD KEY idx_program_payment_lookup (therapist_id, program_id, effective_from, effective_to)'))
    print('Therapy sheet and therapist payout tables are ready.')
