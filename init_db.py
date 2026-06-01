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


def _foreign_key_referenced_table(db: Session, table_name: str, constraint_name: str) -> str | None:
    row = db.execute(
        text(
            """
            SELECT referenced_table_name
            FROM information_schema.key_column_usage
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND constraint_name = :constraint_name
              AND referenced_table_name IS NOT NULL
            LIMIT 1
            """
        ),
        {"table_name": table_name, "constraint_name": constraint_name},
    ).first()
    return row[0] if row else None


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

    valid_ids = ", ".join(str(status_id) for status_id in MASTER_LOOKUP_DATA[category].values())
    db.execute(text(
        f"""
        UPDATE {table_name}
        SET {new_column} = {default_id}
        WHERE {new_column} NOT IN ({valid_ids})
        """
    ))

    db.execute(text(f"ALTER TABLE {table_name} MODIFY {new_column} INT NOT NULL DEFAULT {default_id}"))

    referenced_table = _foreign_key_referenced_table(db, table_name, constraint_name)
    if referenced_table and referenced_table != lookup_table:
        db.execute(text(f"ALTER TABLE {table_name} DROP FOREIGN KEY {constraint_name}"))
        referenced_table = None

    if not referenced_table and not _foreign_key_exists(db, table_name, constraint_name):
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


def _ensure_session_plan_columns(db: Session):
    if _table_exists(db, "patient_session_plan") and not _column_exists(db, "patient_session_plan", "notes"):
        db.execute(text("ALTER TABLE patient_session_plan ADD COLUMN notes TEXT NULL"))

    if _table_exists(db, "patient_session_plan_item") and not _column_exists(db, "patient_session_plan_item", "amount_per_session"):
        db.execute(text(
            "ALTER TABLE patient_session_plan_item "
            "ADD COLUMN amount_per_session DECIMAL(10,2) NOT NULL DEFAULT 0.00"
        ))
    db.commit()


def _ensure_program_table(db: Session):
    if not _table_exists(db, "programs"):
        db.execute(text("""
            CREATE TABLE programs (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                region_id INT NOT NULL,
                program_name VARCHAR(100) NOT NULL,
                per_session_amount FLOAT NOT NULL DEFAULT 1200,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                deleted_at DATETIME NULL,
                UNIQUE KEY uq_program_region_name (region_id, program_name),
                KEY idx_program_region_id (region_id),
                KEY idx_program_active (is_active),
                CONSTRAINT fk_programs_region_id FOREIGN KEY (region_id) REFERENCES regions(id)
            )
        """))

    for column_name, definition in {
        "region_id": "INT NOT NULL",
        "program_name": "VARCHAR(100) NOT NULL DEFAULT 'General'",
        "per_session_amount": "FLOAT NOT NULL DEFAULT 1200",
        "is_active": "TINYINT(1) NOT NULL DEFAULT 1",
        "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        "deleted_at": "DATETIME NULL",
    }.items():
        if not _column_exists(db, "programs", column_name):
            db.execute(text(f"ALTER TABLE programs ADD COLUMN {column_name} {definition}"))

    db.execute(text("""
        INSERT INTO programs (region_id, program_name, per_session_amount, is_active)
        SELECT r.id, 'General', 1200, 1
        FROM regions r
        WHERE r.deleted_at IS NULL
        ON DUPLICATE KEY UPDATE
            is_active = VALUES(is_active),
            per_session_amount = programs.per_session_amount
    """))
    db.commit()


def _ensure_package_region_rows(db: Session):
    if not _table_exists(db, "packages"):
        return

    if not _column_exists(db, "packages", "region_id"):
        db.execute(text("ALTER TABLE packages ADD COLUMN region_id INT NULL"))

    defaults = [
        ("Therapy Package - 12 Sessions", "Standard therapy package", 12, 12000, 90),
        ("Therapy Package - 8 Sessions", "Short therapy package", 8, 8000, 60),
        ("Therapy Package - 16 Sessions", "Extended therapy package", 16, 16000, 120),
    ]
    for name, description, total_sessions, price, duration_days in defaults:
        db.execute(text("""
            INSERT INTO packages (region_id, name, description, total_sessions, price, duration_days, is_active)
            SELECT r.id, :name, :description, :total_sessions, :price, :duration_days, 1
            FROM regions r
            WHERE r.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM packages p
                  WHERE p.region_id = r.id
                    AND p.name = :name
                    AND p.deleted_at IS NULL
              )
        """), {
            "name": name,
            "description": description,
            "total_sessions": total_sessions,
            "price": price,
            "duration_days": duration_days,
        })
    db.commit()


def _ensure_legacy_region_rows(db: Session):
    if not _table_exists(db, "regions"):
        return

    referenced_ids = set()
    for table_name in ("patients", "therapists", "appointments", "invoices"):
        if not _table_exists(db, table_name) or not _column_exists(db, table_name, "region_id"):
            continue
        rows = db.execute(text(f"SELECT DISTINCT region_id FROM {table_name} WHERE region_id IS NOT NULL")).all()
        referenced_ids.update(row[0] for row in rows)

    if _table_exists(db, "user_region_mapping") and _column_exists(db, "user_region_mapping", "regionid"):
        rows = db.execute(text("SELECT DISTINCT regionid FROM user_region_mapping WHERE regionid IS NOT NULL")).all()
        referenced_ids.update(row[0] for row in rows)

    for region_id in sorted(referenced_ids):
        db.execute(text(
            """
            INSERT INTO regions (id, name, code, location)
            VALUES (:id, :name, :code, :location)
            ON DUPLICATE KEY UPDATE id = id
            """
        ), {
            "id": region_id,
            "name": f"Region {region_id}",
            "code": f"REG{region_id}",
            "location": "Legacy",
        })
    db.commit()


def migrate_enums_to_master_lookups(db: Session):
    _seed_master_lookups(db)
    _migrate_lookup_column(db, "invoices", "status", "status_id", "invoice", "draft", "fk_invoices_status_id")
    _migrate_lookup_column(db, "patient_session_plan", "status", "status_id", "patient_session_plan", "ACTIVE", "fk_patient_session_plan_status_id")
    _migrate_lookup_column(db, "patient_assessment", "status", "status_id", "patient_assessment", "PENDING", "fk_patient_assessment_status_id")
    _migrate_lookup_column(db, "patient_slot_booking", "status", "status_id", "patient_slot_booking", "BOOKED", "fk_patient_slot_booking_status_id")
    _migrate_lookup_column(db, "patient_therapy", "status", "status_id", "patient_therapy", "ACTIVE", "fk_patient_therapy_status_id")
    _migrate_lookup_column(db, "therapist_slot_mapping", "status", "status_id", "therapist_slot_mapping", "BOOKED", "fk_therapist_slot_mapping_status_id")
    _migrate_lookup_column(db, "assessment_master", "type", "type_id", "assessment_type", "STRUCTURED", "fk_assessment_master_type_id")
    _migrate_lookup_column(db, "question_master", "question_type", "question_type_id", "question_type", "TEXT", "fk_question_master_question_type_id")
    db.commit()


def align_database_schema():
    db = SessionLocal()
    try:
        _ensure_legacy_region_rows(db)
        migrate_enums_to_master_lookups(db)
        _ensure_patient_assessment_columns(db)
        _ensure_session_plan_columns(db)
        _ensure_program_table(db)
        _ensure_package_region_rows(db)
        print("[OK] Database schema aligned")
    finally:
        db.close()


def init_database():
    align_database_schema()


if __name__ == "__main__":
    align_database_schema()
