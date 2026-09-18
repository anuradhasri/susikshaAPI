"""Run from autism-backend: python -m scripts.migrate_report_sheets"""
from app.core.database import SessionLocal, engine
from app.models import models  # Register existing referenced tables.
from app.services.report_sheet_catalog import provision_report_sheets

if __name__ == '__main__':
    engine.echo = False
    with SessionLocal() as db:
        provision_report_sheets(db)
    print('Report sheet tables and master catalog are ready.')
