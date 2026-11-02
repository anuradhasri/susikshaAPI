"""Add payment-alert tables without creating rules or sending notifications."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect
from app.core.database import SessionLocal
from app.models import models  # Register referenced users, regions and notifications.
from app.models.payment_alerts import (
    PaymentAlertRule, PaymentAlertEvent, PaymentAlertDelivery, PaymentAlertLease,
    provision_payment_alerts,
)


def main():
    with SessionLocal() as db:
        db.get_bind().echo = False
        provision_payment_alerts(db)
        existing = set(inspect(db.get_bind()).get_table_names())
        for model in (PaymentAlertRule, PaymentAlertEvent, PaymentAlertDelivery, PaymentAlertLease):
            if model.__tablename__ not in existing:
                raise RuntimeError(f'Missing table after migration: {model.__tablename__}')
            print(f'{model.__tablename__}: ready')
        print('No alert rules were created or enabled.')


if __name__ == '__main__':
    main()
