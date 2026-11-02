"""Store real completion times; backfill only from completion ledger events."""
from sqlalchemy import inspect, text
from app.core.database import engine
engine.echo = False
with engine.begin() as connection:
    if 'completed_at' not in {column['name'] for column in inspect(connection).get_columns('patient_slot_booking')}:
        connection.execute(text('ALTER TABLE patient_slot_booking ADD COLUMN completed_at DATETIME NULL'))
    connection.execute(text("""UPDATE patient_slot_booking booking
        JOIN (SELECT patient_slot_booking_id AS booking_id, MIN(COALESCE(payment_date, DATE_SUB(created_at, INTERVAL 330 MINUTE))) AS completed_at
              FROM payments WHERE patient_slot_booking_id IS NOT NULL
              AND remark LIKE '%Session completed' GROUP BY patient_slot_booking_id) event
        ON event.booking_id = booking.id
        SET booking.completed_at = event.completed_at
        WHERE booking.completed_at IS NULL"""))
print('Slot completion timestamps are ready.')
