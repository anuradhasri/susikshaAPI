from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, text
from app.core.database import Base


class PaymentAlertRule(Base):
    __tablename__ = 'payment_alert_rule'
    id = Column(Integer, primary_key=True)
    region_id = Column(Integer, ForeignKey('regions.id'), nullable=False)
    kind = Column(String(32), nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    config = Column(JSON, nullable=False)
    recipients = Column(JSON, nullable=False)
    updated_by = Column(Integer, ForeignKey('users.id'))
    __table_args__ = (UniqueConstraint('region_id', 'kind', name='uq_payment_alert_centre_kind'),)


class PaymentAlertEvent(Base):
    __tablename__ = 'payment_alert_event'
    id = Column(Integer, primary_key=True)
    rule_id = Column(Integer, ForeignKey('payment_alert_rule.id'), nullable=False)
    event_key = Column(String(120), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    payload = Column(JSON, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    __table_args__ = (UniqueConstraint('rule_id', 'event_key', name='uq_payment_alert_event'),)


class PaymentAlertDelivery(Base):
    __tablename__ = 'payment_alert_delivery'
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey('payment_alert_event.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    notification_id = Column(Integer, ForeignKey('notifications.id'), nullable=False)
    __table_args__ = (UniqueConstraint('event_id', 'user_id', name='uq_payment_alert_delivery'),)


class PaymentAlertLease(Base):
    __tablename__ = 'payment_alert_lease'
    key = Column(String(32), primary_key=True)
    owner = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)


def provision_payment_alerts(db):
    # Authentication/centre lookups can retain MySQL metadata locks. DDL uses
    # a separate connection, so release that read-only transaction first.
    if db.new or db.dirty or db.deleted:
        raise RuntimeError('Save pending changes before provisioning payment-alert tables.')
    db.rollback()
    with db.get_bind().begin() as connection:
        mysql = connection.dialect.name in {'mysql', 'mariadb'}
        previous = connection.scalar(text('SELECT @@SESSION.lock_wait_timeout')) if mysql else None
        if mysql:
            connection.exec_driver_sql('SET SESSION lock_wait_timeout=10')
        try:
            for model in (PaymentAlertRule, PaymentAlertEvent, PaymentAlertDelivery, PaymentAlertLease):
                model.__table__.create(connection, checkfirst=True)
        finally:
            if mysql:
                connection.exec_driver_sql(f'SET SESSION lock_wait_timeout={int(previous)}')
