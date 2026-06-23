from pathlib import Path

from sqlalchemy import create_engine, text


def database_url() -> str:
    for line in Path(".env").read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL not found")


engine = create_engine(database_url())
with engine.connect() as conn:
    rows = conn.execute(text("""
        SELECT
            p.id AS payment_id,
            p.patient_id,
            p.patient_slot_booking_id,
            p.crt_program_booking_id,
            p.amount,
            p.payment_amount,
            p.due_amount AS payment_due,
            p.fully_paid AS payment_full,
            psb.due_amount AS slot_due,
            psb.fully_paid AS slot_full,
            cpb.due_amount AS crt_due,
            cpb.fully_paid AS crt_full,
            p.created_at
        FROM payments p
        LEFT JOIN patient_slot_booking psb ON psb.id = p.patient_slot_booking_id
        LEFT JOIN crt_program_bookings cpb ON cpb.id = p.crt_program_booking_id
        WHERE p.assessment_source_payment_id IS NULL
          AND (p.patient_slot_booking_id IS NOT NULL OR p.crt_program_booking_id IS NOT NULL)
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT 12
    """)).mappings().all()
    for row in rows:
        print(dict(row))
