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
            p.assessment_source_payment_id,
            p.amount,
            p.payment_amount,
            p.due_amount AS payment_due,
            p.fully_paid AS payment_full,
            p.remark,
            b.id AS billing_id,
            b.assessment_amount,
            b.paid_amount AS billing_paid,
            b.due_amount AS billing_due,
            b.fully_paid AS billing_full,
            p.created_at
        FROM payments p
        LEFT JOIN patient_assessment_billing b
          ON b.source_payment_id = COALESCE(p.assessment_source_payment_id, p.id)
         AND b.patient_id = p.patient_id
        WHERE p.remark LIKE '[assessment:%'
           OR p.remark LIKE '[assessment-payment:%'
           OR p.assessment_source_payment_id IS NOT NULL
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT 20
    """)).mappings().all()
    for row in rows:
        print(dict(row))
