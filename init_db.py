"""
Live database schema alignment.

This project uses the existing MySQL schema as the source of truth. Do not create
tables from SQLAlchemy models here; only run explicit, idempotent changes that the
application needs.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.models import MASTER_LOOKUP_DATA


PATIENT_ASSESSMENT_COLUMNS = {
    "assessment_date": "DATE NULL",
    "assessment_background": "TEXT NULL",
    "assessment_referral_reason": "TEXT NULL",
    "assessment_cooperation": "TEXT NULL",
    "assessment_visual_performance": "TEXT NULL",
    "assessment_imitation_skills": "TEXT NULL",
    "assessment_gross_motor_skills": "TEXT NULL",
    "assessment_fine_motor_skills": "TEXT NULL",
    "assessment_sensory_processing": "TEXT NULL",
    "assessment_play_leisure_skills": "TEXT NULL",
    "assessment_social_skills": "TEXT NULL",
    "assessment_receptive_language": "TEXT NULL",
    "assessment_expressive_language": "TEXT NULL",
    "assessment_pre_skills": "TEXT NULL",
    "assessment_reading_comprehension": "TEXT NULL",
    "assessment_language_communication": "TEXT NULL",
    "assessment_conceptual_awareness": "TEXT NULL",
    "assessment_visual_spatial_time": "TEXT NULL",
    "assessment_mathematics_skills": "TEXT NULL",
    "assessment_attention_processing": "TEXT NULL",
    "assessment_recommendations": "TEXT NULL",
    "assessment_report_notes": "TEXT NULL",
}

LOOKUP_TABLES = {
    "invoice": "invoice_status_master",
    "patient_session_plan": "patient_session_plan_status_master",
    "patient_assessment": "patient_assessment_status_master",
    "patient_slot_booking": "patient_slot_booking_status_master",
    "patient_therapy": "patient_therapy_status_master",
    "therapist_slot_mapping": "therapist_slot_mapping_status_master",
    "assessment_type": "assessment_type_master",
    "question_type": "question_type_master",
}


def _format_status_name(code: str) -> str:
    return code.replace("_", " ").title()


def _table_exists(db: Session, table_name: str) -> bool:
    return bool(db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
            LIMIT 1
            """
        ),
        {"table_name": table_name},
    ).first())


def _column_exists(db: Session, table_name: str, column_name: str) -> bool:
    return bool(db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND column_name = :column_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).first())


def _foreign_key_exists(db: Session, table_name: str, constraint_name: str) -> bool:
    return bool(db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.table_constraints
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND constraint_name = :constraint_name
              AND constraint_type = 'FOREIGN KEY'
            LIMIT 1
            """
        ),
        {"table_name": table_name, "constraint_name": constraint_name},
    ).first())


def _ensure_lookup_table(db: Session, table_name: str):
    if not _table_exists(db, table_name):
        db.execute(text(
            f"""
            CREATE TABLE {table_name} (
                id INT NOT NULL PRIMARY KEY,
                code VARCHAR(100) NOT NULL,
                name VARCHAR(100) NOT NULL,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_{table_name}_code (code),
                KEY idx_{table_name}_active (is_active)
            )
            """
        ))


def _seed_master_lookups(db: Session):
    for category, statuses in MASTER_LOOKUP_DATA.items():
        table_name = LOOKUP_TABLES[category]
        _ensure_lookup_table(db, table_name)
        for code, status_id in statuses.items():
            db.execute(text(
                f"""
                INSERT INTO {table_name} (id, code, name, is_active)
                VALUES (:id, :code, :name, 1)
                ON DUPLICATE KEY UPDATE
                    code = VALUES(code),
                    name = VALUES(name),
                    is_active = VALUES(is_active)
                """
            ), {
                "id": status_id,
                "code": code,
                "name": _format_status_name(code),
            })
    db.commit()


def _migrate_lookup_column(
    db: Session,
    table_name: str,
    old_column: str,
    new_column: str,
    category: str,
    default_code: str,
    constraint_name: str,
):
    default_id = MASTER_LOOKUP_DATA[category][default_code]
    lookup_table = LOOKUP_TABLES[category]

    if not _table_exists(db, table_name):
        return

    if not _column_exists(db, table_name, new_column):
        db.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {new_column} INT NULL"))

    if _column_exists(db, table_name, old_column):
        cases = " ".join(
            f"WHEN UPPER({old_column}) = '{code.upper()}' THEN {status_id}"
            for code, status_id in MASTER_LOOKUP_DATA[category].items()
        )
        db.execute(text(
            f"""
            UPDATE {table_name}
            SET {new_column} = CASE {cases} ELSE {default_id} END
            WHERE {new_column} IS NULL
            """
        ))
        db.execute(text(f"ALTER TABLE {table_name} DROP COLUMN {old_column}"))
    else:
        db.execute(text(
            f"UPDATE {table_name} SET {new_column} = {default_id} WHERE {new_column} IS NULL"
        ))

    db.execute(text(f"ALTER TABLE {table_name} MODIFY {new_column} INT NOT NULL DEFAULT {default_id}"))

    if not _foreign_key_exists(db, table_name, constraint_name):
        db.execute(text(
            f"""
            ALTER TABLE {table_name}
            ADD CONSTRAINT {constraint_name}
            FOREIGN KEY ({new_column}) REFERENCES {lookup_table}(id)
            """
        ))


def _ensure_patient_assessment_columns(db: Session):
    for column_name, definition in PATIENT_ASSESSMENT_COLUMNS.items():
        if not _column_exists(db, "patients", column_name):
            db.execute(text(f"ALTER TABLE patients ADD COLUMN {column_name} {definition}"))
    db.commit()


def migrate_enums_to_master_lookups(db: Session):
    _seed_master_lookups(db)
    _migrate_lookup_column(db, "invoices", "status", "status_id", "invoice", "draft", "fk_invoices_status_id")
    _migrate_lookup_column(db, "patient_session_plan", "status", "status_id", "patient_session_plan", "ACTIVE", "fk_patient_session_plan_status_id")
    _migrate_lookup_column(db, "patient_assessment", "status", "status_id", "patient_assessment", "PENDING", "fk_patient_assessment_status_id")
    _migrate_lookup_column(db, "patient_slot_booking", "status", "status_id", "patient_slot_booking", "BOOKED", "fk_patient_slot_booking_status_id")
    _migrate_lookup_column(db, "patient_therapy", "status", "status_id", "patient_therapy", "ACTIVE", "fk_patient_therapy_status_id")
    _migrate_lookup_column(db, "therapist_slot_mapping", "status", "status_id", "therapist_slot_mapping", "ASSIGNED", "fk_therapist_slot_mapping_status_id")
    _migrate_lookup_column(db, "assessment_master", "type", "type_id", "assessment_type", "STRUCTURED", "fk_assessment_master_type_id")
    _migrate_lookup_column(db, "question_master", "question_type", "question_type_id", "question_type", "TEXT", "fk_question_master_question_type_id")
    db.commit()


def align_database_schema():
    db = SessionLocal()
    try:
        migrate_enums_to_master_lookups(db)
        _ensure_patient_assessment_columns(db)
        print("[OK] Database schema aligned")
    finally:
        db.close()


def init_database():
    align_database_schema()


if __name__ == "__main__":
    align_database_schema()
