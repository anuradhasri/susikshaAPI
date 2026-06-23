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
            b.id AS billing_id,
            b.patient_id,
            b.source_payment_id,
            b.assessment_amount,
            b.paid_amount AS billing_paid,
            b.due_amount AS billing_due,
            b.fully_paid AS billing_full,
            b.payment_status AS billing_status,
            p.id AS payment_id,
            p.amount AS payment_amount_snapshot,
            p.payment_amount,
            p.due_amount AS payment_due,
            p.fully_paid AS payment_full
        FROM patient_assessment_billing b
        LEFT JOIN payments p
          ON p.assessment_source_payment_id = b.source_payment_id
        WHERE b.patient_id = (
            SELECT patient_id
            FROM patient_assessment_billing
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
        )
        ORDER BY b.id, p.id
    """)).mappings().all()
    for row in rows:
        print(dict(row))
