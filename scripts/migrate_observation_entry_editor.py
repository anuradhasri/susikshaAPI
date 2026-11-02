"""Add last-editor metadata without changing existing observations."""
from sqlalchemy import inspect, text
from app.core.database import engine
engine.echo = False
with engine.begin() as connection:
    columns = {c['name'] for c in inspect(connection).get_columns('patient_observation_sheet_entry')}
    for name, sql_type in [('updated_by', 'INT NULL'), ('updated_at', 'DATETIME NULL')]:
        if name not in columns:
            connection.execute(text(f'ALTER TABLE patient_observation_sheet_entry ADD COLUMN {name} {sql_type}'))
print('Observation entry editor fields are ready.')
