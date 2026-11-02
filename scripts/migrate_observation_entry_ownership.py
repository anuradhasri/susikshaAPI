"""Add entry ownership without replacing observation records; safe to rerun."""
from sqlalchemy import inspect, text
from app.core.database import engine

with engine.begin() as connection:
    columns = {c['name'] for c in inspect(connection).get_columns('patient_observation_sheet_entry')}
    if 'created_by' not in columns:
        connection.execute(text('ALTER TABLE patient_observation_sheet_entry ADD COLUMN created_by INT NULL'))
    if 'created_at' not in columns:
        connection.execute(text('ALTER TABLE patient_observation_sheet_entry ADD COLUMN created_at DATETIME NULL'))
    connection.execute(text("""UPDATE patient_observation_sheet_entry e
        JOIN patient_observation_sheet s ON s.id = e.sheet_id
        SET e.created_by = COALESCE(e.created_by, s.created_by),
            e.created_at = COALESCE(e.created_at, s.created_at)
        WHERE e.created_by IS NULL OR e.created_at IS NULL"""))
    connection.execute(text('ALTER TABLE patient_observation_sheet_entry MODIFY created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP'))
print('Observation entry ownership is ready.')
