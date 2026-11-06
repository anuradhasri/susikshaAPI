"""Print missing columns by default; --apply adds only these nullable columns."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, text
from app.core.database import engine

COLUMNS = {
    'patient_observation_sheet_entry': {
        'created_by': 'INT NULL', 'created_at': 'DATETIME NULL',
        'updated_by': 'INT NULL', 'updated_at': 'DATETIME NULL',
    },
    'patient_slot_booking': {'completed_at': 'DATETIME NULL'},
}


def main():
    engine.echo = False
    with engine.begin() as connection:
        for table, columns in COLUMNS.items():
            existing = {column['name'] for column in inspect(connection).get_columns(table)}
            for name, definition in columns.items():
                if name in existing:
                    continue
                statement = f'ALTER TABLE `{table}` ADD COLUMN `{name}` {definition}'
                print(statement)
                if '--apply' in sys.argv:
                    connection.execute(text(statement))
    print('Applied missing columns only.' if '--apply' in sys.argv else 'Preview only; database unchanged.')


if __name__ == '__main__':
    main()
