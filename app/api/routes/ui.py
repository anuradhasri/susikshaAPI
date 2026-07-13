from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
import hashlib
import mimetypes
from pathlib import Path
import re
import secrets
import shutil
import smtplib
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.orm import Session, joinedload, object_session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import decode_token, hash_password, password_exceeds_bcrypt_limit, verify_password
from app.email_templates.password_reset import build_password_reset_email
from app.models.models import (
    Appointment,
    AssessmentMaster,
    AuditLog,
    Document,
    DocumentTypeMaster,
    Enquiry,
    Invoice,
    Package,
    Patient,
    PatientAssessmentBilling,
    PatientDuplicate,
    PatientPackage,
    Program,
    PatientSessionPlan,
    PatientSessionPlanItem,
    PatientSlotBooking,
    SlotMaster,
    TherapistSlotMapping,
    PasswordResetToken,
    Payment,
    PaymentModeMaster,
    Region,
    Role,
    Therapist,
    TherapistAvailability,
    TherapistLeave,
    TherapistTherapyMapping,
    TherapyAssessmentMapping,
    Session as TherapySession,
    SessionNote,
    User,
    UserRegionMapping,
    UserRole,
    MASTER_LOOKUP_DATA,
    CrtProgramBooking,
    GroupProgramBooking,
)
from app.services.user_service import AuthService, UserService

router = APIRouter(prefix="/api/v1/ui", tags=["ui"])
settings = get_settings()
UPLOAD_ROOT = Path(__file__).resolve().parents[3] / "uploads"
DOCUMENT_UPLOAD_ROOT = Path(settings.DOCUMENT_UPLOAD_PATH)
MAX_DOCUMENT_UPLOAD_SIZE_BYTES = settings.MAX_DOCUMENT_UPLOAD_SIZE_MB * 1024 * 1024
ALLOWED_DOCUMENT_EXTENSIONS = {
    ext.strip().lower() if ext.strip().startswith(".") else f".{ext.strip().lower()}"
    for ext in settings.DOCUMENT_ALLOWED_EXTENSIONS.split(",")
    if ext.strip()
}
_PATIENT_PROFILE_SCHEMA_READY = False
_PACKAGE_BILLING_SCHEMA_READY = False
_ASSESSMENT_BILLING_SCHEMA_READY = False
_ENQUIRY_SCHEMA_READY = False
REMOVED_DB_TABLES = {
    "alerts",
    "waitlist_entries",
    "session_assignments",
}


def _ensure_patient_profile_schema(db: Session) -> None:
    global _PATIENT_PROFILE_SCHEMA_READY
    if _PATIENT_PROFILE_SCHEMA_READY:
        return

    columns = {
        col["name"]
        for col in inspect(db.bind).get_columns("patients")
    }
    dialect = db.bind.dialect.name if db.bind else ""
    definitions = {
        "blood_group": "VARCHAR(20)",
        "nationality": "VARCHAR(100)",
        "emergency_phone": "VARCHAR(20)",
        "referred_by": "VARCHAR(255)",
        "registration_at": "DATETIME",
        "profile_photo_path": "VARCHAR(500)",
    }
    for name, definition in definitions.items():
        if name in columns:
            continue
        if dialect == "sqlite":
            db.execute(text(f"ALTER TABLE patients ADD COLUMN {name} {definition}"))
        else:
            db.execute(text(f"ALTER TABLE patients ADD COLUMN {name} {definition} NULL"))
    db.commit()
    _PATIENT_PROFILE_SCHEMA_READY = True


def _ensure_package_billing_schema(db: Session) -> None:
    global _PACKAGE_BILLING_SCHEMA_READY
    def ensure_payment_fully_paid_schema() -> None:
        payment_columns = {col["name"] for col in inspect(db.bind).get_columns("payments")}
        if "patient_slot_booking_id" not in payment_columns:
            db.execute(text("ALTER TABLE payments ADD COLUMN patient_slot_booking_id INT NULL"))
        if "crt_program_booking_id" not in payment_columns:
            db.execute(text("ALTER TABLE payments ADD COLUMN crt_program_booking_id INT NULL"))
        if "assessment_source_payment_id" not in payment_columns:
            db.execute(text("ALTER TABLE payments ADD COLUMN assessment_source_payment_id BIGINT NULL"))
        if "amount" not in payment_columns:
            db.execute(text("ALTER TABLE payments ADD COLUMN amount DECIMAL(12, 2) NOT NULL DEFAULT 0"))
        if "fully_paid" not in payment_columns:
            db.execute(text("ALTER TABLE payments ADD COLUMN fully_paid BOOLEAN NOT NULL DEFAULT 0"))
        if "due_amount" not in payment_columns:
            db.execute(text("ALTER TABLE payments ADD COLUMN due_amount FLOAT NOT NULL DEFAULT 0"))
        if "transaction_id" not in payment_columns:
            db.execute(text("ALTER TABLE payments ADD COLUMN transaction_id VARCHAR(255) NULL"))
        if "payment_note" not in payment_columns:
            db.execute(text("ALTER TABLE payments ADD COLUMN payment_note TEXT NULL"))
    def ensure_crt_program_booking_schema() -> None:
        inspector = inspect(db.bind)
        if inspector.has_table("crt_program_bookings"):
            columns = {col["name"] for col in inspector.get_columns("crt_program_bookings")}
            if "slot_id" not in columns:
                db.execute(text("ALTER TABLE crt_program_bookings ADD COLUMN slot_id INT NULL"))
            return
        db.execute(text("""
            CREATE TABLE crt_program_bookings (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                patient_id INT NOT NULL,
                program_id INT NOT NULL,
                patient_package_id INT NULL,
                slot_id INT NULL,
                slot_date DATE NOT NULL,
                total_amount FLOAT NOT NULL DEFAULT 0,
                paid_amount FLOAT NOT NULL DEFAULT 0,
                package_covered_amount FLOAT NOT NULL DEFAULT 0,
                due_amount FLOAT NOT NULL DEFAULT 0,
                fully_paid BOOLEAN NOT NULL DEFAULT 0,
                is_package_session BOOLEAN NOT NULL DEFAULT 0,
                payment_status VARCHAR(50) NOT NULL DEFAULT 'UNPAID',
                status_id INT NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_crt_program_booking_patient_date (patient_id, program_id, slot_date),
                KEY idx_crt_program_booking_session (patient_id, program_id, slot_date, slot_id),
                KEY idx_crt_program_booking_package_id (patient_package_id),
                KEY idx_crt_program_booking_status_id (status_id)
            )
        """))

    def ensure_group_program_booking_schema() -> None:
        inspector = inspect(db.bind)
        if inspector.has_table("group_program_bookings"):
            return
        db.execute(text("""
            CREATE TABLE group_program_bookings (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                program_id INT NOT NULL,
                region_id INT NULL,
                therapy_id INT NULL,
                slot_id INT NOT NULL,
                slot_date DATE NOT NULL,
                start_time TIME NULL,
                end_time TIME NULL,
                status_id INT NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_group_program_booking_session (program_id, slot_date, slot_id, therapy_id),
                KEY idx_group_program_booking_region_id (region_id),
                KEY idx_group_program_booking_status_id (status_id)
            )
        """))

    def ensure_booking_package_covered_schema() -> None:
        inspector = inspect(db.bind)
        slot_columns = {col["name"] for col in inspector.get_columns("patient_slot_booking")}
        if "package_covered_amount" not in slot_columns:
            db.execute(text("ALTER TABLE patient_slot_booking ADD COLUMN package_covered_amount FLOAT NOT NULL DEFAULT 0"))
        if inspector.has_table("crt_program_bookings"):
            crt_columns = {col["name"] for col in inspector.get_columns("crt_program_bookings")}
            if "package_covered_amount" not in crt_columns:
                db.execute(text("ALTER TABLE crt_program_bookings ADD COLUMN package_covered_amount FLOAT NOT NULL DEFAULT 0"))

    def ensure_group_booking_indexes() -> None:
        inspector = inspect(db.bind)
        indexes = {idx["name"] for idx in inspector.get_indexes("patient_slot_booking")}
        unique_constraints = {idx["name"] for idx in inspector.get_unique_constraints("patient_slot_booking")}
        existing_names = indexes | unique_constraints
        dialect = db.bind.dialect.name if db.bind else ""
        if "uq_one_patient_per_slot" in existing_names:
            if dialect == "mysql":
                if "idx_patient_slot_booking_mapping_id" not in existing_names:
                    db.execute(text(
                        "CREATE INDEX idx_patient_slot_booking_mapping_id "
                        "ON patient_slot_booking (therapist_slot_mapping_id)"
                    ))
                    existing_names.add("idx_patient_slot_booking_mapping_id")
                db.execute(text("ALTER TABLE patient_slot_booking DROP INDEX uq_one_patient_per_slot"))
            else:
                db.execute(text("DROP INDEX IF EXISTS uq_one_patient_per_slot"))
            existing_names.discard("uq_one_patient_per_slot")
        if "uq_patient_slot_mapping_child" not in existing_names:
            if dialect == "sqlite":
                db.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_slot_mapping_child "
                    "ON patient_slot_booking (therapist_slot_mapping_id, patient_id)"
                ))
            else:
                db.execute(text(
                    "ALTER TABLE patient_slot_booking "
                    "ADD CONSTRAINT uq_patient_slot_mapping_child "
                    "UNIQUE (therapist_slot_mapping_id, patient_id)"
                ))

    def backfill_crt_program_bookings() -> None:
        crt_slots = (
            db.query(PatientSlotBooking)
            .join(Program, Program.id == PatientSlotBooking.program_id)
            .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
            .filter(
                PatientSlotBooking.crt_program_booking_id.is_(None),
                PatientSlotBooking.patient_id.isnot(None),
                or_(Program.program_name.ilike("%crt%"), Program.program_name.ilike("%structured%")),
            )
            .all()
        )
        existing_parents = (
            db.query(CrtProgramBooking)
            .filter(CrtProgramBooking.slot_id.is_(None))
            .all()
        )
        for parent in existing_parents:
            linked_slot = (
                db.query(PatientSlotBooking)
                .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
                .filter(PatientSlotBooking.crt_program_booking_id == parent.id)
                .order_by(TherapistSlotMapping.slot_id.asc(), PatientSlotBooking.id.asc())
                .first()
            )
            if linked_slot and linked_slot.therapist_slot_mapping:
                parent.slot_id = linked_slot.therapist_slot_mapping.slot_id

        groups: dict[tuple[int, int, date], list[PatientSlotBooking]] = defaultdict(list)
        for slot in crt_slots:
            if slot.therapist_slot_mapping:
                groups[(slot.patient_id, slot.program_id, slot.therapist_slot_mapping.slot_date)].append(slot)
        for (group_patient_id, group_program_id, group_date), group_slots in groups.items():
            first_mapping = group_slots[0].therapist_slot_mapping
            amount = max(float(_slot_booking_amount(slot) or 0) for slot in group_slots)
            paid = sum(float(slot.paid_amount or 0) for slot in group_slots)
            fully_paid = any(bool(getattr(slot, "fully_paid", False)) for slot in group_slots) or (amount > 0 and paid >= amount)
            parent = CrtProgramBooking(
                patient_id=group_patient_id,
                program_id=group_program_id,
                patient_package_id=next((slot.patient_package_id for slot in group_slots if slot.patient_package_id), None),
                slot_id=first_mapping.slot_id if first_mapping else None,
                slot_date=group_date,
                total_amount=amount,
                paid_amount=paid,
                due_amount=0 if fully_paid else max(0, amount - paid),
                fully_paid=fully_paid,
                is_package_session=any(bool(slot.is_package_session) for slot in group_slots),
                payment_status="PAID" if fully_paid else ("PARTIAL" if paid > 0 else "UNPAID"),
                status_id=group_slots[0].status_id,
            )
            db.add(parent)
            db.flush()
            for slot in group_slots:
                slot.crt_program_booking_id = parent.id
                slot.payment_status = parent.payment_status
                slot.fully_paid = parent.fully_paid
            payable_slot = max(group_slots, key=lambda slot: (float(_slot_booking_amount(slot) or 0), int(slot.id or 0)))
            for slot in group_slots:
                if slot.id != payable_slot.id:
                    slot.amount = 0
                    slot.paid_amount = 0
                    slot.due_amount = 0
            payable_slot.amount = amount
            payable_slot.paid_amount = paid
            payable_slot.due_amount = parent.due_amount

    if _PACKAGE_BILLING_SCHEMA_READY:
        slot_columns = {col["name"] for col in inspect(db.bind).get_columns("patient_slot_booking")}
        if "crt_program_booking_id" not in slot_columns:
            db.execute(text("ALTER TABLE patient_slot_booking ADD COLUMN crt_program_booking_id INT NULL"))
        if "group_program_booking_id" not in slot_columns:
            db.execute(text("ALTER TABLE patient_slot_booking ADD COLUMN group_program_booking_id INT NULL"))
        if "fully_paid" not in slot_columns:
            db.execute(text("ALTER TABLE patient_slot_booking ADD COLUMN fully_paid BOOLEAN NOT NULL DEFAULT 0"))
        ensure_crt_program_booking_schema()
        ensure_group_program_booking_schema()
        ensure_booking_package_covered_schema()
        backfill_crt_program_bookings()
        ensure_group_booking_indexes()
        ensure_payment_fully_paid_schema()
        db.commit()
        return

    dialect = db.bind.dialect.name if db.bind else ""

    def add_columns(table: str, definitions: dict[str, str]) -> None:
        existing = {col["name"] for col in inspect(db.bind).get_columns(table)}
        for name, definition in definitions.items():
            if name in existing:
                continue
            if dialect == "sqlite":
                db.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
            else:
                db.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))

    add_columns("patient_packages", {
        "total_amount": "FLOAT NOT NULL DEFAULT 0",
        "paid_amount": "FLOAT NOT NULL DEFAULT 0",
        "due_amount": "FLOAT NOT NULL DEFAULT 0",
        "payment_status": "VARCHAR(50) NOT NULL DEFAULT 'UNPAID'",
    })
    add_columns("patient_slot_booking", {
        "patient_package_id": "INT NULL",
        "crt_program_booking_id": "INT NULL",
        "group_program_booking_id": "INT NULL",
        "is_package_session": "BOOLEAN NOT NULL DEFAULT 0",
        "amount": "FLOAT NOT NULL DEFAULT 0",
        "paid_amount": "FLOAT NOT NULL DEFAULT 0",
        "package_covered_amount": "FLOAT NOT NULL DEFAULT 0",
        "due_amount": "FLOAT NOT NULL DEFAULT 0",
        "fully_paid": "BOOLEAN NOT NULL DEFAULT 0",
        "payment_status": "VARCHAR(50) NOT NULL DEFAULT 'UNPAID'",
    })
    ensure_payment_fully_paid_schema()
    ensure_crt_program_booking_schema()
    ensure_group_program_booking_schema()
    ensure_booking_package_covered_schema()
    backfill_crt_program_bookings()
    ensure_group_booking_indexes()
    db.commit()
    _PACKAGE_BILLING_SCHEMA_READY = True


def _ensure_assessment_billing_schema(db: Session) -> None:
    global _ASSESSMENT_BILLING_SCHEMA_READY
    if _ASSESSMENT_BILLING_SCHEMA_READY:
        return
    inspector = inspect(db.bind)
    if not inspector.has_table("patient_assessment_billing"):
        db.execute(text("""
            CREATE TABLE patient_assessment_billing (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                patient_id INT NOT NULL,
                assessment_id INT NOT NULL,
                patient_assessment_id INT NULL,
                source_payment_id BIGINT NULL,
                assessment_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
                paid_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
                due_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
                fully_paid BOOLEAN NOT NULL DEFAULT 0,
                payment_status VARCHAR(50) NOT NULL DEFAULT 'UNPAID',
                completed_at DATETIME NULL,
                created_by INT NULL,
                updated_by INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_patient_assessment_billing (patient_id, assessment_id),
                KEY idx_patient_assessment_billing_patient_id (patient_id),
                KEY idx_patient_assessment_billing_assessment_id (assessment_id),
                KEY idx_patient_assessment_billing_patient_assessment_id (patient_assessment_id),
                KEY idx_patient_assessment_billing_source_payment_id (source_payment_id),
                KEY idx_patient_assessment_billing_status (payment_status)
            )
        """))
    else:
        billing_columns = {column["name"] for column in inspector.get_columns("patient_assessment_billing")}
        if "fully_paid" not in billing_columns:
            db.execute(text("ALTER TABLE patient_assessment_billing ADD COLUMN fully_paid BOOLEAN NOT NULL DEFAULT 0"))
            db.execute(text("UPDATE patient_assessment_billing SET fully_paid = CASE WHEN due_amount <= 0 THEN 1 ELSE 0 END"))
    db.commit()
    _ASSESSMENT_BILLING_SCHEMA_READY = True


def _assessment_billing_status(amount: float, paid: float, due: float) -> str:
    if amount <= 0 or due <= 0:
        return "PAID"
    if paid > 0:
        return "PARTIAL"
    return "UNPAID"


def _ensure_enquiry_schema(db: Session) -> None:
    global _ENQUIRY_SCHEMA_READY
    if _ENQUIRY_SCHEMA_READY:
        return

    inspector = inspect(db.bind)
    dialect = db.bind.dialect.name if db.bind else ""

    if not inspector.has_table("enquiries"):
        if dialect == "sqlite":
            db.execute(text("""
                CREATE TABLE enquiries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name VARCHAR(100) NOT NULL,
                    last_name VARCHAR(100) NULL,
                    gender VARCHAR(20) NULL,
                    phone VARCHAR(20) NOT NULL,
                    referred_by VARCHAR(255) NULL,
                    enquiry_source VARCHAR(100) NULL,
                    program_id INTEGER NULL,
                    region_id INTEGER NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT 1
                )
            """))
        else:
            db.execute(text("""
                CREATE TABLE enquiries (
                    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    first_name VARCHAR(100) NOT NULL,
                    last_name VARCHAR(100) NULL,
                    gender VARCHAR(20) NULL,
                    phone VARCHAR(20) NOT NULL,
                    referred_by VARCHAR(255) NULL,
                    enquiry_source VARCHAR(100) NULL,
                    program_id INT NULL,
                    region_id INT NOT NULL,
                    is_active TINYINT(1) NOT NULL DEFAULT 1,
                    KEY idx_enquiry_program_id (program_id),
                    KEY idx_enquiry_region_id (region_id),
                    KEY idx_enquiry_phone (phone),
                    KEY idx_enquiry_is_active (is_active)
                )
            """))
        db.commit()
        _ENQUIRY_SCHEMA_READY = True
        return

    columns = {col["name"] for col in inspector.get_columns("enquiries")}
    definitions = {
        "first_name": "VARCHAR(100) NULL",
        "last_name": "VARCHAR(100) NULL",
        "gender": "VARCHAR(20) NULL",
        "phone": "VARCHAR(20) NULL",
        "referred_by": "VARCHAR(255) NULL",
        "enquiry_source": "VARCHAR(100) NULL",
        "program_id": "INT NULL",
        "region_id": "INT NULL",
        "is_active": "BOOLEAN NOT NULL DEFAULT 1",
    }
    sqlite_definitions = {
        name: definition.replace("INT", "INTEGER")
        for name, definition in definitions.items()
    }
    for name, definition in (sqlite_definitions if dialect == "sqlite" else definitions).items():
        if name not in columns:
            db.execute(text(f"ALTER TABLE enquiries ADD COLUMN {name} {definition}"))
    db.commit()
    _ENQUIRY_SCHEMA_READY = True


def _now() -> datetime:
    return datetime.utcnow()


def _client_ip(request: Request) -> Optional[str]:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _password_reset_url(token: str) -> str:
    base_url = settings.FRONTEND_URL.rstrip("/")
    return f"{base_url}/reset-password?{urlencode({'token': token})}"


def _password_policy_error(password: str) -> Optional[str]:
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if password_exceeds_bcrypt_limit(password):
        return "Password cannot be longer than 72 bytes"
    if not any(char.isupper() for char in password):
        return "Password must include at least one uppercase letter"
    if not any(char.isdigit() for char in password):
        return "Password must include at least one number"
    if not any(not char.isalnum() for char in password):
        return "Password must include at least one special character"
    return None


def _change_password_policy_error(password: str) -> Optional[str]:
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if password_exceeds_bcrypt_limit(password):
        return "Password cannot be longer than 72 bytes"
    return None


def _write_audit_log(
    db: Session,
    *,
    user_id: Optional[int],
    entity_type: str,
    entity_id: int,
    action: str,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
    request: Optional[Request] = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            old_values=old_values,
            new_values=new_values,
            ip_address=_client_ip(request) if request else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
    )


def _send_password_reset_email(email: str, reset_url: str) -> bool:
    if not settings.email_host:
        return False

    message = build_password_reset_email(
        to_email=email,
        from_email=settings.email_from,
        reset_url=reset_url,
        expires_in_minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
    )

    smtp_class = smtplib.SMTP_SSL if settings.email_use_ssl else smtplib.SMTP
    with smtp_class(settings.email_host, settings.email_port, timeout=15) as smtp:
        if settings.email_use_tls:
            smtp.starttls()
        if settings.email_username:
            smtp.login(settings.email_username, settings.email_password)
        smtp.send_message(message)
    return True


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _time_with_duration(start: Optional[time], duration_minutes: Optional[int]) -> Optional[time]:
    if not start or not duration_minutes:
        return None
    base = datetime.combine(date.today(), start) + timedelta(minutes=int(duration_minutes))
    return base.time()


def _full_name(user: Optional[User]) -> str:
    if not user:
        return "Unknown"
    return f"{user.first_name} {user.last_name}".strip()


def _table_exists(db: Session, table_name: str) -> bool:
    try:
        return inspect(db.bind).has_table(table_name)
    except Exception:
        return False


def _user_region_ids(db: Session, user: Optional[User]) -> list[int]:
    if not user:
        return []
    try:
        region_ids = [
            region_id
            for (region_id,) in (
                db.query(UserRegionMapping.regionid)
                .filter(UserRegionMapping.userid == user.id)
                .all()
            )
        ]
        if region_ids:
            return region_ids
        therapist = (
            db.query(Therapist.region_id)
            .filter(Therapist.user_id == user.id, Therapist.is_active.is_(True))
            .first()
        )
        return [therapist.region_id] if therapist and therapist.region_id else []
    except Exception:
        return []


def _primary_region_id(db: Session, user: Optional[User]) -> Optional[int]:
    region_ids = _user_region_ids(db, user)
    return region_ids[0] if region_ids else None


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "User", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _document_type(value: Optional[str]) -> str:
    return {
        "general": "OTHER",
        "report": "PROGRESS_REPORT",
        "progress_report": "PROGRESS_REPORT",
        "consent": "CONSENT_FORM",
        "consent_form": "CONSENT_FORM",
        "prescription": "OTHER",
        "id": "OTHER",
        "insurance": "OTHER",
    }.get((value or "").lower(), (value or "other").upper())


def _document_type_id(db: Session, value: Optional[str]) -> int:
    name = _document_type(value)
    row = db.query(DocumentTypeMaster).filter(
        func.upper(DocumentTypeMaster.name) == name,
        DocumentTypeMaster.status == 1,
    ).first()
    if row:
        return row.id
    fallback = db.query(DocumentTypeMaster).filter(func.upper(DocumentTypeMaster.name) == "OTHER").first()
    if fallback:
        return fallback.id
    fallback = DocumentTypeMaster(name="OTHER", status=1)
    db.add(fallback)
    db.flush()
    return fallback.id


def _document_type_label(db: Optional[Session], document_type_id: Optional[int]) -> str:
    if not db or not document_type_id:
        return "other"
    row = db.query(DocumentTypeMaster).filter(DocumentTypeMaster.id == document_type_id).first()
    return (row.name if row else "OTHER").lower()


def _safe_filename(filename: str) -> str:
    clean = "".join(char if char.isalnum() or char in {".", "_", "-"} else "_" for char in filename)
    return clean.strip("._") or "document"


def _document_file_path(document: Document) -> Path:
    document_root = DOCUMENT_UPLOAD_ROOT.resolve()
    relative_path = Path(document.file_path)
    if relative_path.is_absolute():
        path = relative_path.resolve()
    elif document.file_path.replace("\\", "/").startswith("uploads/"):
        path = (Path(__file__).resolve().parents[3] / document.file_path).resolve()
        document_root = UPLOAD_ROOT.resolve()
    else:
        path = (DOCUMENT_UPLOAD_ROOT / relative_path).resolve()

    if not str(path).startswith(str(document_root)) or not path.exists():
        raise HTTPException(status_code=404, detail="Document file not found")
    return path


def _patient_photo_file_path(patient: Patient) -> Path:
    if not patient.profile_photo_path:
        raise HTTPException(status_code=404, detail="Patient photo not found")

    normalized_path = patient.profile_photo_path.replace("\\", "/").lstrip("/")
    if normalized_path.startswith("uploads/"):
        photo_root = UPLOAD_ROOT.resolve()
        path = (Path(__file__).resolve().parents[3] / normalized_path).resolve()
    else:
        photo_root = DOCUMENT_UPLOAD_ROOT.resolve()
        path = (DOCUMENT_UPLOAD_ROOT / Path(normalized_path)).resolve()

    if not str(path).startswith(str(photo_root)) or not path.exists():
        raise HTTPException(status_code=404, detail="Patient photo file not found")
    return path


def _current_user(request: Request, db: Session) -> Optional[User]:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    payload = decode_token(auth.split(" ", 1)[1])
    if not payload:
        return None
    user_id = payload.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()


def _require_user(request: Request, db: Session) -> User:
    user = _current_user(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def _default_region(db: Session) -> Region:
    region = db.query(Region).filter(Region.deleted_at.is_(None)).first()
    if region:
        return region
    region = Region(name="Default Region", code="DEFAULT", location="Local")
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


def _role(db: Session, name: str) -> Role:
    role = db.query(Role).filter(Role.name == name, Role.deleted_at.is_(None)).first()
    if role:
        return role
    role = Role(name=name, description=f"{name.replace('_', ' ').title()} role")
    db.add(role)
    db.commit()
    db.refresh(role)
    return role

RBAC_RESOURCES = [
    ("menu.appointments", "menu", "Appointments", None, 10),
    ("menu.enquiries", "menu", "Enquiries", None, 20),
    ("menu.children", "menu", "Children", None, 30),
    ("menu.therapists", "menu", "Therapists", None, 40),
    ("menu.reports", "menu", "Reports", None, 50),
    ("region.switch", "control", "Region switcher", None, 60),
    ("appointment.waitlist", "panel", "Appointment waitlist", None, 70),
    ("appointment.action.create", "action", "Create appointment", None, 80),
    ("appointment.action.edit", "action", "Edit appointment", None, 90),
    ("appointment.action.complete", "action", "Complete appointment", None, 100),
    ("appointment.action.payment", "action", "Appointment payment", None, 110),
    ("appointment.action.cancel_paid", "action", "Paid cancel", None, 120),
    ("appointment.action.cancel_unpaid", "action", "Unpaid cancel", None, 130),
    ("appointment.filter.therapists", "control", "Appointment therapist filter", None, 135),
    ("child.tab.details", "tab", "Child details", None, 140),
    ("child.tab.assessment", "tab", "Assessment", None, 150),
    ("child.tab.therapy", "tab", "Therapy", None, 160),
    ("child.tab.payments", "tab", "Payments", None, 170),
    ("child.action.create", "action", "Create child", None, 180),
    ("child.action.edit_profile", "action", "Edit child profile", None, 190),
    ("child.action.upload_document", "action", "Upload child document", None, 200),
    ("child.action.buy_package", "action", "Buy package", None, 210),
    ("child.action.session_plan", "action", "Session plan", None, 220),
    ("child.action.transaction", "action", "Child transaction", None, 230),
    ("assessment.action.export", "action", "Export assessment", None, 240),
    ("assessment.action.complete", "action", "Complete assessment", None, 250),
]

FRONT_OFFICE_PERMISSION_CODES = {code for code, *_ in RBAC_RESOURCES}
CREATE_PERMISSION_CODES = {
    "appointment.action.create",
    "child.action.create",
    "child.action.upload_document",
    "child.action.buy_package",
    "child.action.transaction",
}
EDIT_PERMISSION_CODES = {
    "appointment.action.edit",
    "appointment.action.complete",
    "appointment.action.payment",
    "child.action.edit_profile",
    "child.action.session_plan",
    "assessment.action.complete",
    "region.switch",
}
DELETE_PERMISSION_CODES = {
    "appointment.action.cancel_paid",
    "appointment.action.cancel_unpaid",
}
THERAPIST_PERMISSION_CODES = {
    "menu.appointments",
    "child.tab.details",
    "child.tab.assessment",
    "child.tab.therapy",
    "assessment.action.export",
}
CENTRAL_HEAD_PERMISSION_CODES = {
    "menu.appointments",
    "appointment.filter.therapists",
    "child.tab.details",
    "child.tab.assessment",
    "assessment.action.export",
    "assessment.action.complete",
}


def _ensure_rbac_tables(db: Session) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS rbac_resources (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            code VARCHAR(120) NOT NULL UNIQUE,
            resource_type VARCHAR(50) NOT NULL,
            label VARCHAR(150) NOT NULL,
            parent_code VARCHAR(120) NULL,
            display_order INT NOT NULL DEFAULT 0,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_rbac_resources_type (resource_type),
            KEY idx_rbac_resources_active (is_active)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS rbac_role_permissions (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            role_id INT NOT NULL,
            resource_id INT NOT NULL,
            can_view TINYINT(1) NOT NULL DEFAULT 0,
            can_create TINYINT(1) NOT NULL DEFAULT 0,
            can_edit TINYINT(1) NOT NULL DEFAULT 0,
            can_delete TINYINT(1) NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_rbac_role_resource (role_id, resource_id),
            KEY idx_rbac_role_permissions_role (role_id),
            KEY idx_rbac_role_permissions_resource (resource_id)
        )
    """))
    for code, resource_type, label, parent_code, display_order in RBAC_RESOURCES:
        db.execute(text("""
            INSERT INTO rbac_resources (code, resource_type, label, parent_code, display_order, is_active)
            VALUES (:code, :resource_type, :label, :parent_code, :display_order, 1)
            ON DUPLICATE KEY UPDATE
                resource_type = VALUES(resource_type),
                label = VALUES(label),
                parent_code = VALUES(parent_code),
                display_order = VALUES(display_order),
                is_active = 1
        """), {
            "code": code,
            "resource_type": resource_type,
            "label": label,
            "parent_code": parent_code,
            "display_order": display_order,
        })

    for role_name in ("admin", "front_office", "frontoffice", "front_officer", "therapist", "central_head"):
        _role(db, role_name)

    role_permissions = {
        "admin": FRONT_OFFICE_PERMISSION_CODES,
        "front_office": FRONT_OFFICE_PERMISSION_CODES,
        "frontoffice": FRONT_OFFICE_PERMISSION_CODES,
        "front_officer": FRONT_OFFICE_PERMISSION_CODES,
        "therapist": THERAPIST_PERMISSION_CODES,
        "central_head": CENTRAL_HEAD_PERMISSION_CODES,
    }
    for role_name, codes in role_permissions.items():
        role = _role(db, role_name)
        for code, *_ in RBAC_RESOURCES:
            enabled = code in codes
            db.execute(text("""
                INSERT INTO rbac_role_permissions (
                    role_id, resource_id, can_view, can_create, can_edit, can_delete
                )
                SELECT :role_id, resource.id, :can_view, :can_create, :can_edit, :can_delete
                FROM rbac_resources resource
                WHERE resource.code = :code
                ON DUPLICATE KEY UPDATE
                    can_view = VALUES(can_view),
                    can_create = VALUES(can_create),
                    can_edit = VALUES(can_edit),
                    can_delete = VALUES(can_delete)
            """), {
                "role_id": role.id,
                "code": code,
                "can_view": 1 if enabled else 0,
                "can_create": 1 if enabled and code in CREATE_PERMISSION_CODES else 0,
                "can_edit": 1 if enabled and code in EDIT_PERMISSION_CODES else 0,
                "can_delete": 1 if enabled and code in DELETE_PERMISSION_CODES else 0,
            })
    db.commit()


def _permission_shape(db: Session, user: User) -> dict:
    rows = db.execute(text("""
        SELECT
            resource.code,
            MAX(permission.can_view) AS can_view,
            MAX(permission.can_create) AS can_create,
            MAX(permission.can_edit) AS can_edit,
            MAX(permission.can_delete) AS can_delete
        FROM rbac_resources resource
        JOIN rbac_role_permissions permission ON permission.resource_id = resource.id
        JOIN user_roles user_role ON user_role.role_id = permission.role_id
        WHERE user_role.user_id = :user_id
          AND user_role.deleted_at IS NULL
          AND resource.is_active = 1
        GROUP BY resource.code
    """), {"user_id": user.id}).all()
    return {
        row.code: {
            "view": bool(row.can_view),
            "create": bool(row.can_create),
            "edit": bool(row.can_edit),
            "delete": bool(row.can_delete),
        }
        for row in rows
    }


def _assessment_permission_shape(db: Session, user: User, permissions: Optional[dict] = None) -> dict:
    permissions = permissions if permissions is not None else _permission_shape(db, user)
    role_names = _user_roles(db, user)
    can_view_assessment = permissions.get("child.tab.assessment", {}).get("view", False)
    can_edit_assessment = permissions.get("assessment.action.complete", {}).get("edit", False)
    if "central_head" in role_names:
        can_view_assessment = True
        can_edit_assessment = True
    elif "therapist" in role_names:
        can_view_assessment = True
        can_edit_assessment = False
    result = {}
    rows = db.query(AssessmentMaster).filter(AssessmentMaster.is_active.is_(True)).all()
    for assessment in rows:
        can_edit_this_assessment = can_edit_assessment
        result[str(assessment.id)] = {
            "view": can_view_assessment,
            "edit": can_edit_this_assessment,
            "complete": can_edit_this_assessment,
        }
    return result


def _user_shape(user: User, db: Session) -> dict:
    region_ids = _user_region_ids(db, user)
    permissions = _permission_shape(db, user)

    return {
        "id": user.id,
        "user_id": user.id,
        "username": user.username,
        "full_name": _full_name(user),
        "email": user.email,
        "phone": user.phone,
        "region_id": region_ids[0] if region_ids else None,
        "region_ids": region_ids,
        "roles": [
            {
                "role_id": ur.role.id,
                "role_name": ur.role.name
            }
            for ur in user.user_roles
            if ur.deleted_at is None and ur.role and ur.role.deleted_at is None
        ],
        "user_roles": [
            {
                "roles": {
                    "id": ur.role.id,
                    "name": ur.role.name,
                }
            }
            for ur in user.user_roles
            if ur.deleted_at is None and ur.role and ur.role.deleted_at is None
        ],
        "permissions": permissions,
        "assessment_permissions": _assessment_permission_shape(db, user, permissions),
    }


def _user_shape_from_model(user: User) -> dict:
    db = object_session(user)
    if not db:
        return {
            "id": user.id,
            "username": user.username,
            "full_name": _full_name(user),
            "email": user.email,
            "phone": user.phone,
            "region_id": None,
            "region_ids": [],
        }
    return _user_shape(user, db)


def _role_shape(role: Role) -> dict:
    return {
        "id": role.id,
        "name": role.name,
        "description": role.description,
        "created_at": _iso(role.created_at),
        "updated_at": _iso(role.updated_at),
        "deleted_at": _iso(role.deleted_at),
    }


def _user_roles(db: Session, user: Optional[User]) -> set[str]:
    if not user:
        return set()
    return {
        name
        for (name,) in (
            db.query(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .filter(UserRole.user_id == user.id, UserRole.deleted_at.is_(None), Role.deleted_at.is_(None))
            .all()
        )
    }


def _is_therapist_scoped_role(roles: set[str]) -> bool:
    return bool(roles & {"therapist", "assessment_session", "assessment_viewer_session"})


def _current_therapist(db: Session, user: Optional[User]) -> Optional[Therapist]:
    if not user:
        return None

    therapist = (
        db.query(Therapist)
        .filter(Therapist.user_id == user.id, Therapist.is_active.is_(True))
        .first()
    )
    if therapist:
        return therapist

    full_name = _full_name(user)
    if not full_name:
        return None

    return (
        db.query(Therapist)
        .filter(Therapist.name == full_name, Therapist.is_active.is_(True))
        .first()
    )


def _user_role_shape(user_role: UserRole) -> dict:
    return {
        "id": user_role.id,
        "user_id": user_role.user_id,
        "role_id": user_role.role_id,
        "assigned_at": _iso(user_role.created_at),
    }


def _patient_shape(patient: Patient) -> dict:
    full_name = f"{patient.first_name} {patient.last_name}".strip()
    profile_photo_url = None
    if patient.profile_photo_path:
        version = int(patient.updated_at.timestamp()) if patient.updated_at else int(datetime.utcnow().timestamp())
        profile_photo_url = f"api/v1/ui/patients/{patient.id}/photo?v={version}"
    return {
        "id": patient.id,
        "patient_code": f"PT-{patient.id:04d}",
        "full_name": full_name,
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "date_of_birth": _iso(patient.date_of_birth),
        "gender": patient.gender.lower() if patient.gender else None,
        "phone": patient.phone,
        "email": patient.email,
        "father_name": patient.father_name,
        "mother_name": patient.mother_name,
        "blood_group": patient.blood_group,
        "nationality": patient.nationality,
        "address": patient.address,
        "emergency_contact": patient.alternate_contact,
        "emergency_phone": patient.emergency_phone,
        "referred_by": patient.referred_by,
        "registration_at": _iso(patient.registration_at or patient.created_at),
        "profile_photo_path": patient.profile_photo_path,
        "profile_photo_url": profile_photo_url,
        "clinical_observation": patient.clinical_observation,
        "diagnosis": patient.diagnosis,
        "notes": patient.notes,
        "is_active": True,
        "is_available": bool(patient.is_available),
        "region_id": patient.region_id,
        "created_at": _iso(patient.created_at),
        "updated_at": _iso(patient.updated_at),
        "deleted_at": None,
        "created_by": patient.created_by,
        "updated_by": patient.updated_by,
        "assessment_answers": patient.assessment_answers,
    }


def _enquiry_shape(enquiry: Enquiry) -> dict:
    return {
        "id": enquiry.id,
        "first_name": enquiry.first_name,
        "last_name": enquiry.last_name,
        "gender": enquiry.gender,
        "phone": enquiry.phone,
        "referred_by": enquiry.referred_by,
        "enquiry_source": enquiry.enquiry_source,
        "program_id": getattr(enquiry, "program_id", None),
        "program_name": enquiry.program.program_name if getattr(enquiry, "program", None) else None,
        "region_id": enquiry.region_id,
        "is_active": enquiry.is_active,
    }


def _therapist_shape(therapist: Therapist) -> dict:
    linked_user = getattr(therapist, "user", None)
    linked_name = _full_name(linked_user) if linked_user else None
    leave = _therapist_leave_for_date(therapist, date.today())
    on_full_day_leave = _leave_session_is_full_day(getattr(leave, "leave_session", None)) if leave else False
    active_therapy_names = [
        mapping.therapy.name
        for mapping in (getattr(therapist, "therapy_mappings", None) or [])
        if getattr(mapping, "is_active", False) and getattr(mapping, "therapy", None)
    ]
    return {
        "id": therapist.id,
        "user_id": therapist.user_id,
        "name": therapist.name,
        "specialization": ", ".join(active_therapy_names) or None,
        "qualification": therapist.qualification,
        "license_number": None,
        "bio": therapist.qualification,
        "max_patients": 30,
        "region_id": therapist.region_id,
        "is_active": bool(therapist.is_active),
        "is_available": bool(therapist.is_active) and not on_full_day_leave,
        "availability_status": "leave" if leave else ("available" if therapist.is_active else "inactive"),
        "leave_date": _iso(leave.leave_date) if leave else None,
        "leave_session": leave.leave_session if leave else None,
        "leave_reason": leave.reason if leave else None,
        "created_at": _iso(therapist.created_at),
        "updated_at": _iso(therapist.updated_at),
        "deleted_at": None,
        "created_by": therapist.created_by,
        "updated_by": therapist.updated_by,
        "users": {
            "full_name": therapist.name or linked_name,
            "email": linked_user.email if linked_user else None,
            "phone": linked_user.phone if linked_user else None,
        },
        "user": _user_shape_from_model(linked_user) if linked_user else None,
    }


def _leave_session_is_full_day(leave_session: str | None) -> bool:
    normalized = str(leave_session or "full_day").strip().lower().replace(" ", "_")
    return normalized in {"full_day", "fullday", "full", "off_day", "offday", "leave"}


def _therapist_leave_for_date(therapist: Therapist, leave_date: date) -> Optional[TherapistLeave]:
    db = object_session(therapist)
    if db is None:
        return None
    return (
        db.query(TherapistLeave)
        .filter(
            TherapistLeave.therapist_id == therapist.id,
            TherapistLeave.leave_date == leave_date,
        )
        .first()
    )


def _availability_status_requests_leave(status_value: str | None) -> bool:
    normalized = str(status_value or "").strip().lower().replace(" ", "_")
    return normalized in {"leave", "off_day", "offday", "full_day", "full"}


def _sync_therapist_leave_from_availability(
    db: Session,
    therapist_id: int,
    leave_date: date,
    status_value: str | None,
    notes: str | None,
    user_id: Optional[int],
) -> None:
    existing = (
        db.query(TherapistLeave)
        .filter(
            TherapistLeave.therapist_id == therapist_id,
            TherapistLeave.leave_date == leave_date,
        )
        .first()
    )
    if _availability_status_requests_leave(status_value):
        leave_session = "full_day"
        if existing:
            existing.leave_session = leave_session
            existing.reason = notes
            existing.updated_by = user_id
            return
        db.add(TherapistLeave(
            therapist_id=therapist_id,
            leave_date=leave_date,
            leave_session=leave_session,
            reason=notes,
            created_by=user_id,
            updated_by=user_id,
        ))
    elif existing:
        db.delete(existing)


def _minutes_between(start: Optional[time], end: Optional[time]) -> int:
    if not start or not end:
        return 0
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    return max(0, end_minutes - start_minutes)


def _availability_minutes(row: "TherapistAvailability") -> int:
    work_minutes = _minutes_between(row.start_time, row.end_time)
    break_minutes = _minutes_between(row.break_start, row.break_end)
    return max(0, work_minutes - break_minutes)


def _availability_shape(row: TherapistAvailability) -> dict:
    therapist = row.therapist
    leave = _therapist_leave_for_date(therapist, row.availability_date) if therapist else None
    status = "leave" if leave and _leave_session_is_full_day(leave.leave_session) else ("half" if leave else row.status)
    return {
        "id": row.id,
        "therapist_id": row.therapist_id,
        "availability_date": _iso(row.availability_date),
        "start_time": _iso(row.start_time),
        "end_time": _iso(row.end_time),
        "break_start": _iso(row.break_start),
        "break_end": _iso(row.break_end),
        "available_minutes": _availability_minutes(row),
        "status": status,
        "notes": leave.reason if leave and leave.reason else row.notes,
        "leave_session": leave.leave_session if leave else None,
        "leave_reason": leave.reason if leave else None,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "deleted_at": _iso(row.deleted_at),
        "therapists": _therapist_shape(therapist) if therapist else None,
    }


def _therapist_leave_shape(row: TherapistLeave) -> dict:
    therapist = row.therapist
    return {
        "id": row.id,
        "therapist_id": row.therapist_id,
        "leave_date": _iso(row.leave_date),
        "leave_session": row.leave_session,
        "reason": row.reason,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "therapists": _therapist_shape(therapist) if therapist else None,
    }


def _therapist_leave_availability_shape(row: TherapistLeave) -> dict:
    therapist = row.therapist
    leave_session = row.leave_session or "full_day"
    return {
        "id": -int(row.id),
        "therapist_id": row.therapist_id,
        "availability_date": _iso(row.leave_date),
        "start_time": "09:00:00",
        "end_time": "17:00:00",
        "break_start": None,
        "break_end": None,
        "available_minutes": 0,
        "status": "half" if not _leave_session_is_full_day(leave_session) else "leave",
        "notes": row.reason,
        "leave_session": leave_session,
        "leave_reason": row.reason,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "deleted_at": None,
        "therapists": _therapist_shape(therapist) if therapist else None,
    }


def _leave_session_blocks_time(leave_session: str | None, slot_start: time | None) -> bool:
    if not slot_start:
        return True

    normalized = str(leave_session or "full_day").strip().lower().replace(" ", "_")
    if normalized in {"full_day", "fullday", "full"}:
        return True
    if normalized in {"morning", "first_half", "forenoon"}:
        return slot_start.hour < 13
    if normalized in {"afternoon", "second_half", "half_day"}:
        return slot_start.hour >= 13
    return True


def _active_bookings_for_therapist_leave(
    db: Session,
    therapist_id: int,
    leave_date: date,
    leave_session: str | None,
) -> list[PatientSlotBooking]:
    rows = (
        db.query(PatientSlotBooking)
        .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
        .join(SlotMaster, SlotMaster.id == TherapistSlotMapping.slot_id)
        .filter(
            TherapistSlotMapping.therapist_id == therapist_id,
            TherapistSlotMapping.slot_date == leave_date,
            TherapistSlotMapping.status_id != MASTER_LOOKUP_DATA["therapist_slot_mapping"]["CANCELLED"],
            PatientSlotBooking.status_id.notin_([
                MASTER_LOOKUP_DATA["patient_slot_booking"]["UNPAID_CANCELLED"],
                MASTER_LOOKUP_DATA["patient_slot_booking"]["PAID_CANCELLED"],
            ]),
        )
        .all()
    )
    return [
        booking
        for booking in rows
        if _leave_session_blocks_time(leave_session, booking.therapist_slot_mapping.slot.start_time)
    ]


def _appointment_shape(appointment: Any) -> dict:
    duration = max(0, int((appointment.end_time - appointment.start_time).total_seconds() / 60))
    return {
        "id": appointment.id,
        "patient_id": appointment.patient_id,
        "therapist_id": appointment.therapist_id,
        "patient_package_id": None,
        "scheduled_at": _iso(appointment.start_time),
        "start_time": _iso(appointment.start_time),
        "end_time": _iso(appointment.end_time),
        "duration_minutes": duration,
        "status": appointment.status.value if hasattr(appointment.status, "value") else appointment.status,
        "notes": appointment.notes,
        "cancelled_reason": None,
        "created_at": _iso(appointment.created_at),
        "updated_at": _iso(appointment.updated_at),
        "deleted_at": _iso(appointment.deleted_at),
        "created_by": None,
        "updated_by": None,
        "patients": _patient_shape(appointment.patient) if appointment.patient else None,
        "therapists": _therapist_shape(appointment.therapist) if appointment.therapist else None,
    }


def _session_shape(session: Any) -> dict:
    return {
        "id": session.id,
        "appointment_id": session.appointment_id,
        "patient_id": session.patient_id,
        "therapist_id": session.therapist_id,
        "patient_package_id": None,
        "session_number": session.session_number,
        "session_date": _iso(session.session_date),
        "start_time": None,
        "end_time": None,
        "duration_minutes": session.duration_minutes,
        "status": session.status.value if hasattr(session.status, "value") else session.status,
        "is_billed": session.billing_status in {"billed", "paid"},
        "billing_status": session.billing_status,
        "progress_notes": session.progress_notes,
        "created_at": _iso(session.created_at),
        "updated_at": _iso(session.updated_at),
        "deleted_at": _iso(session.deleted_at),
        "created_by": None,
        "updated_by": None,
        "patients": _patient_shape(session.patient) if session.patient else None,
        "therapists": _therapist_shape(session.therapist) if session.therapist else None,
    }


def _package_session_stats(patient_package: PatientPackage) -> dict:
    total = patient_package.package.total_sessions if patient_package.package else (
        (patient_package.sessions_completed or 0) + (patient_package.sessions_remaining or 0)
    )
    package_sessions = []
    completed = _package_billable_usage_count(patient_package)
    scheduled = 0
    remaining = max(0, int(total or 0) - completed)
    return {
        "total": total,
        "completed": completed,
        "scheduled": scheduled,
        "remaining": remaining,
        "sessions": package_sessions,
    }


def _today_unpaid_completed_slot_query(db: Session, patient_id: int):
    return (
        db.query(PatientSlotBooking)
        .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
        .outerjoin(PatientSessionPlanItem, PatientSessionPlanItem.id == PatientSlotBooking.patient_session_plan_item_id)
        .outerjoin(PatientSessionPlan, PatientSessionPlan.id == PatientSessionPlanItem.patient_session_plan_id)
        .filter(
            or_(
                PatientSlotBooking.patient_id == patient_id,
                PatientSessionPlan.patient_id == patient_id,
            ),
            TherapistSlotMapping.slot_date == date.today(),
            PatientSlotBooking.patient_package_id.is_(None),
            PatientSlotBooking.status == "COMPLETED",
            func.upper(PatientSlotBooking.payment_status).in_(["UNPAID", "PENDING", "PARTIAL"]),
        )
        .order_by(PatientSlotBooking.created_at.asc(), PatientSlotBooking.id.asc())
    )


def _slot_amount(slot_booking: PatientSlotBooking) -> float:
    patient_package = slot_booking.patient_package
    if slot_booking.is_package_session and patient_package and patient_package.package and patient_package.package.total_sessions:
        return float(patient_package.total_amount or patient_package.package.price or 0) / float(patient_package.package.total_sessions or 1)
    if slot_booking.amount:
        return float(slot_booking.amount or 0)
    plan_item = slot_booking.patient_session_plan_item
    if plan_item and plan_item.amount_per_session:
        return float(plan_item.amount_per_session or 0)
    if slot_booking.program and slot_booking.program.per_session_amount:
        return float(slot_booking.program.per_session_amount or 0)
    return 0


def _patient_package_preview(db: Session, patient_id: int, package_id: int) -> dict:
    package = db.query(Package).filter(Package.id == package_id, Package.deleted_at.is_(None)).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")

    today_unpaid_sessions = _today_unpaid_completed_slot_query(db, patient_id).all()
    today_count = len(today_unpaid_sessions)
    package_sessions = int(package.total_sessions or 0)
    sessions_adjusted = min(today_count, package_sessions)
    sessions_still_unpaid = max(0, today_count - sessions_adjusted)
    package_remaining = max(0, package_sessions - sessions_adjusted)
    total_unpaid_amount = sum(_slot_amount(row) for row in today_unpaid_sessions)

    return {
        "patient_id": patient_id,
        "package_id": package_id,
        "package_name": package.name,
        "today_unpaid_sessions": today_count,
        "total_unpaid_amount": total_unpaid_amount,
        "package_sessions": package_sessions,
        "sessions_adjusted": sessions_adjusted,
        "today_sessions_still_unpaid": sessions_still_unpaid,
        "package_remaining_sessions": package_remaining,
    }


def _package_total_sessions(patient_package: PatientPackage) -> int:
    return int(
        (patient_package.package.total_sessions if patient_package.package else 0)
        or patient_package.total_sessions
        or (patient_package.sessions_completed or 0) + (patient_package.sessions_remaining or 0)
        or 0
    )


def _package_per_session_rate(patient_package: PatientPackage) -> float:
    total_sessions = _package_total_sessions(patient_package)
    total_amount = float(patient_package.total_amount or (patient_package.package.price if patient_package.package else 0) or 0)
    return total_amount / total_sessions if total_sessions > 0 else 0


def _package_billable_slot_bookings(patient_package: PatientPackage) -> list[PatientSlotBooking]:
    db = object_session(patient_package)
    if not db or not patient_package.id:
        return []

    used_status_ids = [MASTER_LOOKUP_DATA["patient_slot_booking"]["COMPLETED"]]
    return (
        db.query(PatientSlotBooking)
        .options(
            joinedload(PatientSlotBooking.patient_session_plan_item),
            joinedload(PatientSlotBooking.program),
            joinedload(PatientSlotBooking.therapist_slot_mapping),
        )
        .filter(
            PatientSlotBooking.patient_package_id == patient_package.id,
            PatientSlotBooking.is_package_session.is_(True),
            PatientSlotBooking.status_id.in_(used_status_ids),
        )
        .order_by(PatientSlotBooking.created_at.asc(), PatientSlotBooking.id.asc())
        .all()
    )


def _package_billable_usage_rows(patient_package: PatientPackage) -> list[PatientSlotBooking]:
    slots = _package_billable_slot_bookings(patient_package)
    grouped_rows: dict[tuple[Any, ...], PatientSlotBooking] = {}
    rows_without_crt: list[PatientSlotBooking] = []
    for slot_booking in slots:
        if getattr(slot_booking, "group_program_booking_id", None) and slot_booking.patient_id:
            mapping = slot_booking.therapist_slot_mapping
            slot = mapping.slot if mapping else None
            group_key = (
                "group-program-child",
                int(slot_booking.group_program_booking_id),
                int(slot_booking.patient_id),
                getattr(mapping, "slot_date", None),
                getattr(slot, "start_time", None),
                getattr(slot, "end_time", None),
            )
        elif slot_booking.crt_program_booking_id:
            mapping = slot_booking.therapist_slot_mapping
            slot = mapping.slot if mapping else None
            group_key = (
                "crt-parent",
                int(slot_booking.crt_program_booking_id),
                getattr(mapping, "slot_date", None),
                getattr(slot, "start_time", None),
                getattr(slot, "end_time", None),
                0 if mapping and getattr(mapping, "slot_date", None) and slot else int(slot_booking.id or 0),
            )
        else:
            program_name = (slot_booking.program.program_name if slot_booking.program else "") or ""
            mapping = slot_booking.therapist_slot_mapping
            slot_date = mapping.slot_date if mapping else None
            if slot_date and ("crt" in program_name.lower() or "structured" in program_name.lower()):
                slot = mapping.slot if mapping else None
                group_key = (
                    "crt-date",
                    slot_booking.program_id,
                    slot_date,
                    getattr(slot, "start_time", None),
                    getattr(slot, "end_time", None),
                    slot_booking.id,
                )
            else:
                group_key = ()

        if group_key:
            current = grouped_rows.get(group_key)
            if not current or float(slot_booking.amount or _slot_amount(slot_booking) or 0) > float(current.amount or _slot_amount(current) or 0):
                grouped_rows[group_key] = slot_booking
        else:
            rows_without_crt.append(slot_booking)
    return [*grouped_rows.values(), *rows_without_crt]


def _package_billable_usage_count(patient_package: PatientPackage) -> int:
    rows = _package_billable_usage_rows(patient_package)
    if rows:
        return len(rows)
    return int(patient_package.sessions_completed or 0) if not object_session(patient_package) else 0


def _package_utilized_amount(patient_package: PatientPackage) -> float:
    rows = _package_billable_usage_rows(patient_package)
    if rows:
        db = object_session(patient_package)
        utilized_amount = 0.0
        for row in rows:
            crt_parent = row.crt_program_booking
            amount = float((crt_parent.total_amount if crt_parent else row.amount) or _slot_amount(row) or 0)
            if db and _slot_marked_fully_paid(db, row):
                covered = float((crt_parent.package_covered_amount if crt_parent else getattr(row, "package_covered_amount", 0)) or 0)
                paid = float((crt_parent.paid_amount if crt_parent else getattr(row, "paid_amount", 0)) or 0)
                utilized_amount += min(amount, paid + covered)
                continue
            utilized_amount += amount
        return utilized_amount
    return float(patient_package.sessions_completed or 0) * _package_per_session_rate(patient_package) if not object_session(patient_package) else 0.0


def _package_due_amount(patient_package: PatientPackage) -> float:
    utilized_amount = _package_utilized_amount(patient_package)
    paid_amount = float(patient_package.paid_amount or 0)
    return utilized_amount - paid_amount


def _session_payable_due_cap(amount: float, paid: float = 0) -> float:
    return max(0, float(amount or 0) - float(paid or 0))


def _package_direct_paid_amount(patient_package: PatientPackage) -> float:
    db = object_session(patient_package)
    if not db or not patient_package.id:
        return float(patient_package.paid_amount or 0)
    total = (
        db.query(func.coalesce(func.sum(Payment.payment_amount), 0))
        .filter(
            Payment.patient_id == patient_package.patient_id,
            Payment.payment_amount > 0,
            Payment.remark.like(f"[package:{patient_package.id}]%"),
        )
        .scalar()
    )
    return float(total or 0)


def _sync_patient_package_paid_amount(patient_package: Optional[PatientPackage]) -> None:
    if not patient_package:
        return
    patient_package.paid_amount = _package_direct_paid_amount(patient_package) + _package_slot_paid_amount(patient_package)


def _recalculate_package_due_with_paid_amount(db: Session, patient_package: PatientPackage, paid_amount: float) -> float:
    original_paid_amount = float(patient_package.paid_amount or 0)
    patient_package.paid_amount = paid_amount
    _recalculate_package_slot_payments(db, patient_package, sync_paid_amount=False)
    due_amount = float(patient_package.due_amount or 0)
    patient_package.paid_amount = original_paid_amount
    return due_amount


def _package_latest_ledger_due(patient_package: PatientPackage) -> Optional[float]:
    db = object_session(patient_package)
    if not db or not patient_package.id:
        return None
    usage_rows = _package_billable_usage_rows(patient_package)
    slot_ids = [row.id for row in usage_rows if row.id]
    crt_ids = list({int(row.crt_program_booking_id) for row in usage_rows if row.crt_program_booking_id})
    filters = [
        Payment.remark.like(f"[package:{patient_package.id}]%"),
    ]
    if slot_ids:
        filters.append(Payment.patient_slot_booking_id.in_(slot_ids))
    if crt_ids:
        filters.append(Payment.crt_program_booking_id.in_(crt_ids))
    latest_payment = (
        db.query(Payment)
        .filter(
            Payment.patient_id == patient_package.patient_id,
            or_(*filters),
        )
        .order_by(Payment.created_at.desc(), Payment.id.desc())
        .first()
    )
    return float(latest_payment.due_amount) if latest_payment else None


def _sync_patient_package_due_from_ledger(patient_package: Optional[PatientPackage]) -> None:
    if not patient_package:
        return
    db = object_session(patient_package)
    if not db or not patient_package.id:
        return
    usage_rows = _package_billable_usage_rows(patient_package)
    slot_ids = [row.id for row in usage_rows if row.id]
    crt_ids = list({int(row.crt_program_booking_id) for row in usage_rows if row.crt_program_booking_id})
    related_filters = [Payment.remark.like(f"[package:{patient_package.id}]%")]
    if slot_ids:
        related_filters.append(Payment.patient_slot_booking_id.in_(slot_ids))
        related_filters.extend(Payment.remark.like(f"[slot:{slot_id}]%") for slot_id in slot_ids)
    if crt_ids:
        related_filters.append(Payment.crt_program_booking_id.in_(crt_ids))
    payments = (
        db.query(Payment)
        .filter(
            Payment.patient_id == patient_package.patient_id,
            or_(*related_filters),
        )
        .all()
    )
    positive_due = [
        float(payment.due_amount or 0)
        for payment in payments
        if not bool(getattr(payment, "fully_paid", False))
        and float(payment.due_amount or 0) > 0
        and not str(payment.remark or "").startswith(f"[package:{patient_package.id}]")
    ]
    if positive_due:
        patient_package.due_amount = max(positive_due)
        return
    credit_due = [
        float(payment.due_amount or 0)
        for payment in payments
        if float(payment.due_amount or 0) < 0
    ]
    patient_package.due_amount = min(credit_due) if credit_due else 0


def _package_slot_paid_amount(patient_package: PatientPackage, *, include_fully_paid: bool = False) -> float:
    db = object_session(patient_package)
    if not db or not patient_package.id:
        return 0.0
    total = 0.0
    for row in _package_billable_usage_rows(patient_package):
        crt_parent = row.crt_program_booking
        total += float((crt_parent.paid_amount if crt_parent else getattr(row, "paid_amount", 0)) or 0)
    return total


def _package_slot_covered_amount(patient_package: PatientPackage) -> float:
    db = object_session(patient_package)
    if not db or not patient_package.id:
        return 0.0
    total = 0.0
    seen_crt_parent_ids: set[int] = set()
    for row in _package_billable_usage_rows(patient_package):
        crt_parent = row.crt_program_booking
        if crt_parent:
            if int(crt_parent.id) in seen_crt_parent_ids:
                continue
            seen_crt_parent_ids.add(int(crt_parent.id))
            total += float(getattr(crt_parent, "package_covered_amount", 0) or 0)
        else:
            total += float(getattr(row, "package_covered_amount", 0) or 0)
    return total


def _set_patient_package_due_from_usage(patient_package: PatientPackage) -> float:
    db = object_session(patient_package)
    due_amount = 0.0
    if db and patient_package.id:
        seen_crt_parent_ids: set[int] = set()
        for row in _package_billable_usage_rows(patient_package):
            crt_parent = row.crt_program_booking
            if row.crt_program_booking_id:
                if int(row.crt_program_booking_id) in seen_crt_parent_ids:
                    continue
                seen_crt_parent_ids.add(int(row.crt_program_booking_id))
            if bool(getattr(row, "fully_paid", False)) or bool(getattr(crt_parent, "fully_paid", False)) or _slot_marked_fully_paid(db, row):
                continue
            amount = float((crt_parent.total_amount if crt_parent else row.amount) or _slot_amount(row) or 0)
            paid = float((crt_parent.paid_amount if crt_parent else getattr(row, "paid_amount", 0)) or 0)
            covered = float((crt_parent.package_covered_amount if crt_parent else getattr(row, "package_covered_amount", 0)) or 0)
            due_amount += max(0, amount - paid - covered)
    else:
        due_amount = _package_due_amount(patient_package)
    patient_package.due_amount = due_amount
    return due_amount


def _normalize_patient_package_due_values(packages: list[PatientPackage]) -> dict[int, float]:
    normalized: dict[int, float] = {}
    for patient_package in packages:
        if not patient_package or not patient_package.id:
            continue
        normalized[int(patient_package.id)] = _rebuild_patient_package_payment_due_snapshots(patient_package)
    return normalized


def _rebuild_patient_package_payment_due_snapshots(patient_package: PatientPackage) -> float:
    db = object_session(patient_package)
    if not db or not patient_package.id:
        return _set_patient_package_due_from_usage(patient_package)

    usage_rows = _package_billable_usage_rows(patient_package)
    slot_ids = [int(row.id) for row in usage_rows if row.id]
    crt_ids = list({int(row.crt_program_booking_id) for row in usage_rows if row.crt_program_booking_id})
    related_filters = [Payment.remark.like(f"[package:{patient_package.id}]%")]
    if slot_ids:
        related_filters.append(Payment.patient_slot_booking_id.in_(slot_ids))
        related_filters.extend(Payment.remark.like(f"[slot:{slot_id}]%") for slot_id in slot_ids)
    if crt_ids:
        related_filters.append(Payment.crt_program_booking_id.in_(crt_ids))

    payments = (
        db.query(Payment)
        .filter(
            Payment.patient_id == patient_package.patient_id,
            or_(*related_filters),
        )
        .order_by(Payment.created_at.asc(), Payment.payment_date.asc(), Payment.id.asc())
        .all()
    )
    if not payments:
        return _set_patient_package_due_from_usage(patient_package)

    slots_by_id = {int(row.id): row for row in usage_rows if row.id}
    slots_by_crt_id = {int(row.crt_program_booking_id): row for row in usage_rows if row.crt_program_booking_id}
    running_due = 0.0
    charged_usage_keys: set[tuple[str, int]] = set()
    for payment in payments:
        remark = str(payment.remark or "")
        is_package_payment = remark.startswith(f"[package:{patient_package.id}]")
        payment_amount = float(payment.payment_amount or 0)
        if is_package_payment:
            running_due -= payment_amount
            payment.due_amount = running_due
            continue

        row = None
        if getattr(payment, "patient_slot_booking_id", None):
            row = slots_by_id.get(int(payment.patient_slot_booking_id))
        if row is None and getattr(payment, "crt_program_booking_id", None):
            row = slots_by_crt_id.get(int(payment.crt_program_booking_id))

        amount = float(payment.amount or 0)
        if amount <= 0 and row is not None:
            crt_parent = row.crt_program_booking
            amount = float((crt_parent.total_amount if crt_parent else row.amount) or _slot_amount(row) or 0)

        usage_key = None
        if row is not None and getattr(row, "crt_program_booking_id", None):
            usage_key = ("crt", int(row.crt_program_booking_id))
        elif row is not None and getattr(row, "id", None):
            usage_key = ("slot", int(row.id))
        elif getattr(payment, "crt_program_booking_id", None):
            usage_key = ("crt", int(payment.crt_program_booking_id))
        elif getattr(payment, "patient_slot_booking_id", None):
            usage_key = ("slot", int(payment.patient_slot_booking_id))

        charge_amount = 0.0
        if usage_key and amount > 0 and usage_key not in charged_usage_keys:
            charge_amount = amount
            charged_usage_keys.add(usage_key)

        if bool(getattr(payment, "fully_paid", False)):
            waived_amount = _payment_waived_amount(payment)
            running_due += charge_amount
            running_due = max(0, running_due - payment_amount - waived_amount) if running_due > 0 else running_due - payment_amount
            payment.due_amount = 0 if running_due <= 0 else running_due
            continue

        running_due += charge_amount
        running_due -= payment_amount
        payment.due_amount = running_due if running_due < 0 else max(0, running_due)

    patient_package.due_amount = running_due
    return float(patient_package.due_amount or 0)


def _package_slot_waived_amount(patient_package: PatientPackage) -> float:
    db = object_session(patient_package)
    if not db or not patient_package.id:
        return 0.0
    usage_rows = [
        row
        for row in _package_billable_usage_rows(patient_package)
        if not bool(getattr(row, "fully_paid", False))
    ]
    slot_ids = [row.id for row in usage_rows if row.id]
    crt_ids = list({int(row.crt_program_booking_id) for row in usage_rows if row.crt_program_booking_id})
    filters = []
    if slot_ids:
        filters.append(Payment.patient_slot_booking_id.in_(slot_ids))
        filters.extend(Payment.remark.like(f"[slot:{slot_id}]%") for slot_id in slot_ids)
    if crt_ids:
        filters.append(Payment.crt_program_booking_id.in_(crt_ids))
    if not filters:
        return 0.0
    payments = (
        db.query(Payment)
        .filter(
            Payment.patient_id == patient_package.patient_id,
            Payment.fully_paid.is_(True),
            Payment.payment_note.like("%Marked as fully paid%"),
            or_(*filters),
        )
        .all()
    )
    return sum(
        _payment_waived_amount(payment)
        for payment in payments
    )


def _payment_waived_amount(payment: Payment) -> float:
    note = str(getattr(payment, "payment_note", "") or "")
    match = re.search(r"Waived Rs\s*([0-9]+(?:\.[0-9]+)?)", note, flags=re.I)
    if match:
        return max(0.0, float(match.group(1) or 0))
    return max(0.0, float(payment.amount or 0) - float(payment.payment_amount or 0))


def _record_package_payment_allocations(db: Session, patient_package: PatientPackage, paid_amount: float, user_id: Optional[int]) -> None:
    if paid_amount <= 0:
        return
    remaining_credit = paid_amount
    usage_rows = _package_billable_usage_rows(patient_package)
    for slot_booking in usage_rows:
        if remaining_credit <= 0:
            break
        amount = float(slot_booking.amount or _slot_booking_amount(slot_booking) or 0)
        paid = float(slot_booking.paid_amount or 0)
        waived = _slot_waived_amount(db, slot_booking)
        remaining_need = max(0, amount - paid - waived)
        if remaining_need <= 0:
            continue
        applied = min(remaining_credit, remaining_need)
        remaining_credit -= applied
        due = max(0, amount - paid - waived - applied)
        _set_slot_payment_from_amounts(slot_booking, amount, paid, due, applied)
        _sync_crt_parent_payment_from_slot(slot_booking.crt_program_booking, slot_booking)
        patient_id = slot_booking.patient_id or patient_package.patient_id
        db.add(Payment(
            patient_id=patient_id,
            patient_slot_booking_id=slot_booking.id if not slot_booking.crt_program_booking_id else None,
            crt_program_booking_id=slot_booking.crt_program_booking_id,
            amount=amount,
            payment_amount=0,
            payment_status="PAID",
            fully_paid=due <= 0,
            due_amount=due,
            payment_date=_now(),
            remark=f"[slot:{slot_booking.id}] Payment done for Package credit used",
            payment_note=f"Allocated from package payment {patient_package.id}",
            created_by=user_id,
            updated_by=user_id,
        ))


def _sync_package_credit_payment_row(
    db: Session,
    slot_booking: PatientSlotBooking,
    amount: float,
    due: float,
    user_id: Optional[int] = None,
) -> None:
    patient_id = slot_booking.patient_id
    if not patient_id and slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
        patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
    if not patient_id and slot_booking.patient_package:
        patient_id = slot_booking.patient_package.patient_id
    if not patient_id:
        return
    payment = Payment(
        patient_id=patient_id,
        patient_slot_booking_id=slot_booking.id if not slot_booking.crt_program_booking_id else None,
        crt_program_booking_id=slot_booking.crt_program_booking_id,
        amount=amount,
        payment_amount=0,
        payment_status="PAID",
        fully_paid=due <= 0,
        due_amount=due,
        payment_date=_now(),
        remark=f"[slot:{slot_booking.id}] Payment done for Package credit used",
        payment_note=f"Allocated from package payment {slot_booking.patient_package_id}",
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(payment)


def _package_effective_paid_amount(patient_package: PatientPackage) -> float:
    return float(patient_package.paid_amount or 0)


def _package_paid_balance_amount(patient_package: PatientPackage) -> float:
    return max(0, -_package_due_amount(patient_package))


def _package_shape(patient_package: PatientPackage) -> dict:
    stats = _package_session_stats(patient_package)
    due_amount = float(patient_package.due_amount or 0)
    paid_amount = float(patient_package.paid_amount or 0)
    total_amount = float(patient_package.total_amount or (patient_package.package.price if patient_package.package else 0) or 0)
    effective_status = "completed" if int(stats["remaining"] or 0) <= 0 and due_amount <= 0 else "active"
    return {
        "id": patient_package.id,
        "patient_id": patient_package.patient_id,
        "package_id": patient_package.package_id,
        "package_name": patient_package.package.name if patient_package.package else None,
        "program_id": patient_package.package.program_id if patient_package.package else None,
        "program_name": patient_package.package.program.program_name if patient_package.package and patient_package.package.program else None,
        "total_sessions": stats["total"],
        "sessions_used": stats["completed"],
        "sessions_completed": stats["completed"],
        "sessions_remaining": stats["remaining"],
        "remaining_sessions": stats["remaining"],
        "total_amount": total_amount,
        "paid_amount": paid_amount,
        "paid_balance_amount": max(0, paid_amount - total_amount),
        "utilized_amount": _package_utilized_amount(patient_package),
        "per_session_rate": _package_per_session_rate(patient_package),
        "due_amount": due_amount,
        "payment_status": patient_package.payment_status,
        "status": effective_status,
        "created_at": _iso(patient_package.created_at),
        "updated_at": _iso(patient_package.updated_at),
        "deleted_at": _iso(patient_package.deleted_at),
    }


def _master_package_shape(package: Package) -> dict:
    return {
        "id": package.id,
        "name": package.name,
        "description": package.description,
        "region_id": package.region_id,
        "program_id": package.program_id,
        "program_name": package.program.program_name if getattr(package, "program", None) else None,
        "total_sessions": package.total_sessions,
        "price": float(package.price or 0),
        "duration_days": package.duration_days,
        "is_active": package.is_active,
        "created_at": _iso(package.created_at),
        "updated_at": _iso(package.updated_at),
        "deleted_at": _iso(package.deleted_at),
    }


def _ensure_package_master_schema(db: Session) -> None:
    package_columns = {col["name"] for col in inspect(db.bind).get_columns("packages")}
    if "region_id" not in package_columns:
        db.execute(text("ALTER TABLE packages ADD COLUMN region_id INT NULL"))
    if "program_id" not in package_columns:
        db.execute(text("ALTER TABLE packages ADD COLUMN program_id INT NULL"))
    if inspect(db.bind).has_table("programs"):
        seed_names = (
            "General 16 Session - Package",
            "General 12 Session - Package",
            "CRT 5 Session - Package",
            "Vocational 8 Session - Package",
        )
        db.query(Package).filter(Package.program_id.isnot(None), Package.name.notin_(seed_names)).update(
            {"is_active": False},
            synchronize_session=False,
        )
        package_seed = """
            SELECT 'General' AS program_name, 'General 12 Session - Package' AS package_name, 12 AS total_sessions, 13200 AS price, 30 AS duration_days
            UNION ALL SELECT 'General', 'General 16 Session - Package', 16, 17600, 120
            UNION ALL SELECT 'CRT', 'CRT 5 Session - Package', 5, 45000, 30
            UNION ALL SELECT 'CRT (Structured Program)', 'CRT 5 Session - Package', 5, 45000, 30
            UNION ALL SELECT 'Vocational', 'Vocational 8 Session - Package', 8, 10000, 60
            UNION ALL SELECT 'Vocational Program', 'Vocational 8 Session - Package', 8, 10000, 60
        """
        db.execute(text(f"""
            UPDATE packages pkg
            JOIN programs p ON p.id = pkg.program_id
            JOIN ({package_seed}) seed
              ON seed.program_name = p.program_name
             AND seed.package_name = pkg.name
            SET pkg.total_sessions = seed.total_sessions,
                pkg.price = seed.price,
                pkg.duration_days = seed.duration_days,
                pkg.description = CONCAT(p.program_name, ' package'),
                pkg.region_id = p.region_id,
                pkg.is_active = 1
            WHERE p.deleted_at IS NULL
              AND p.is_active = 1
              AND p.region_id = 1
              AND pkg.deleted_at IS NULL
        """))
        db.execute(text(f"""
            INSERT INTO packages (
                name,
                description,
                region_id,
                program_id,
                total_sessions,
                price,
                duration_days,
                is_active
            )
            SELECT
                seed.package_name,
                CONCAT(p.program_name, ' package'),
                p.region_id,
                p.id,
                seed.total_sessions,
                seed.price,
                seed.duration_days,
                1
            FROM programs p
            JOIN ({package_seed}) seed ON seed.program_name = p.program_name
            WHERE p.deleted_at IS NULL
              AND p.is_active = 1
              AND p.region_id = 1
              AND NOT EXISTS (
                SELECT 1
                FROM packages existing
                WHERE existing.program_id = p.id
                  AND existing.name = seed.package_name
                  AND existing.deleted_at IS NULL
              )
        """))
    db.commit()


def _invoice_shape(invoice: Invoice) -> dict:
    total = invoice.total_amount or 0
    paid = invoice.paid_amount or 0
    status_value = invoice.status.value if hasattr(invoice.status, "value") else invoice.status
    status_map = {"issued": "sent"}
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "patient_id": invoice.patient_id,
        "patient_package_id": None,
        "subtotal": total,
        "discount": 0,
        "tax": 0,
        "total": total,
        "total_amount": total,
        "paid_amount": paid,
        "balance": max(0, total - paid),
        "status": status_map.get(status_value, status_value),
        "due_date": _iso(invoice.due_date),
        "issue_date": _iso(invoice.issue_date),
        "notes": invoice.description,
        "created_at": _iso(invoice.created_at),
        "updated_at": _iso(invoice.updated_at),
        "deleted_at": _iso(invoice.deleted_at),
        "patients": _patient_shape(invoice.patient) if invoice.patient else None,
    }


def _payment_shape(payment: Payment) -> dict:
    payment_mode = payment.payment_mode_master.payment_mode_name if payment.payment_mode_master else None
    status_value = payment.payment_status or "PENDING"
    return {
        "id": payment.id,
        "invoice_id": None,
        "patient_id": payment.patient_id,
        "amount": float(payment.payment_amount or 0),
        "payment_amount": float(payment.payment_amount or 0),
        "payment_method": payment_mode,
        "payment_mode": payment_mode,
        "payment_date": _iso(payment.payment_date or payment.created_at),
        "reference_number": None,
        "transaction_id": getattr(payment, "transaction_id", None),
        "notes": payment.remark,
        "payment_remark": getattr(payment, "payment_note", None) or payment.remark,
        "fully_paid": bool(getattr(payment, "fully_paid", False)),
        "due_amount": float(getattr(payment, "due_amount", 0) or 0),
        "status": status_value.lower(),
        "payment_status": status_value,
        "created_at": _iso(payment.created_at),
        "updated_at": _iso(payment.updated_at),
    }


def _slot_booking_amount(slot_booking: PatientSlotBooking) -> float:
    patient_package = slot_booking.patient_package
    if slot_booking.is_package_session and patient_package and patient_package.package and patient_package.package.total_sessions:
        return float(patient_package.total_amount or patient_package.package.price or 0) / float(patient_package.package.total_sessions or 1)
    if slot_booking.amount:
        return float(slot_booking.amount or 0)
    plan_item = slot_booking.patient_session_plan_item
    if plan_item and plan_item.amount_per_session:
        return float(plan_item.amount_per_session or 0)
    if slot_booking.program and slot_booking.program.per_session_amount:
        return float(slot_booking.program.per_session_amount or 0)
    return 0


def _non_package_slot_due(slot_booking: PatientSlotBooking) -> float:
    if bool(getattr(slot_booking, "is_package_session", False)):
        return 0.0
    if bool(getattr(slot_booking, "fully_paid", False)):
        return 0.0
    amount = float(getattr(slot_booking, "amount", 0) or _slot_booking_amount(slot_booking) or 0)
    paid = float(getattr(slot_booking, "paid_amount", 0) or 0)
    stored_due = float(getattr(slot_booking, "due_amount", 0) or 0)
    if stored_due > 0:
        return stored_due
    return max(0, amount - paid)


def _is_package_fully_paid(patient_package: Optional[PatientPackage]) -> bool:
    if not patient_package:
        return False
    total = float(patient_package.total_amount or (patient_package.package.price if patient_package.package else 0) or 0)
    paid = float(patient_package.paid_amount or 0)
    due = max(0, total - paid)
    return total > 0 and paid >= total and due <= 0


def _set_slot_payment_from_amounts(slot_booking: PatientSlotBooking, amount: float, paid: float, due: float, package_applied: float = 0) -> None:
    slot_booking.amount = amount
    slot_booking.paid_amount = paid
    slot_booking.package_covered_amount = package_applied
    slot_booking.due_amount = due
    if not bool(getattr(slot_booking, "is_package_session", False)) and not getattr(slot_booking, "patient_package_id", None):
        slot_booking.fully_paid = bool(amount > 0 and due <= 0)
    if due <= 0 and amount > 0 and package_applied > 0:
        slot_booking.payment_status = "PACKAGE_COVERED"
    elif due <= 0 and amount > 0:
        slot_booking.payment_status = "PAID"
    elif paid >= amount and amount > 0:
        slot_booking.payment_status = "PAID"
    elif package_applied > 0:
        slot_booking.payment_status = "PARTIAL"
    elif paid > 0:
        slot_booking.payment_status = "PARTIAL"
    else:
        slot_booking.payment_status = "UNPAID"


def _set_crt_payment_from_amounts(crt_parent: Optional[CrtProgramBooking], amount: float, paid: float, due: float, package_applied: float = 0) -> None:
    if not crt_parent:
        return
    crt_parent.total_amount = amount
    crt_parent.paid_amount = paid
    crt_parent.package_covered_amount = package_applied
    crt_parent.due_amount = due
    crt_parent.fully_paid = bool(crt_parent.fully_paid or (amount > 0 and due <= 0))
    if due <= 0 and amount > 0 and package_applied > 0:
        crt_parent.payment_status = "PACKAGE_COVERED"
    elif due <= 0 and amount > 0:
        crt_parent.payment_status = "PAID"
    elif paid >= amount and amount > 0:
        crt_parent.payment_status = "PAID"
    elif package_applied > 0 or paid > 0:
        crt_parent.payment_status = "PARTIAL"
    else:
        crt_parent.payment_status = "UNPAID"


def _slot_waived_amount(db: Session, slot_booking: PatientSlotBooking) -> float:
    filters = []
    if slot_booking.id:
        filters.append(Payment.patient_slot_booking_id == slot_booking.id)
        filters.append(Payment.remark.like(f"[slot:{slot_booking.id}]%"))
    if slot_booking.crt_program_booking_id:
        filters.append(Payment.crt_program_booking_id == slot_booking.crt_program_booking_id)
    if not filters:
        return 0.0
    patient_id = slot_booking.patient_id
    if not patient_id and slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
        patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
    if not patient_id and slot_booking.patient_package:
        patient_id = slot_booking.patient_package.patient_id
    if not patient_id and slot_booking.crt_program_booking and slot_booking.crt_program_booking.patient_id:
        patient_id = slot_booking.crt_program_booking.patient_id
    payments = (
        db.query(Payment)
        .filter(
            Payment.patient_id == patient_id,
            Payment.fully_paid.is_(True),
            or_(*filters),
        )
        .all()
    )
    return sum(
        _payment_waived_amount(payment)
        for payment in payments
    )


def _slot_marked_fully_paid(db: Session, slot_booking: PatientSlotBooking) -> bool:
    filters = []
    if slot_booking.id:
        filters.append(Payment.patient_slot_booking_id == slot_booking.id)
        filters.append(Payment.remark.like(f"[slot:{slot_booking.id}]%"))
    if slot_booking.crt_program_booking_id:
        filters.append(Payment.crt_program_booking_id == slot_booking.crt_program_booking_id)
    if not filters:
        return False
    patient_id = slot_booking.patient_id
    if not patient_id and slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
        patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
    if not patient_id and slot_booking.patient_package:
        patient_id = slot_booking.patient_package.patient_id
    if not patient_id and slot_booking.crt_program_booking and slot_booking.crt_program_booking.patient_id:
        patient_id = slot_booking.crt_program_booking.patient_id
    return bool(
        db.query(Payment.id)
        .filter(
            Payment.patient_id == patient_id,
            Payment.fully_paid.is_(True),
            or_(*filters),
        )
        .first()
    )


def _sync_crt_parent_payment_from_slot(crt_parent: Optional[CrtProgramBooking], slot_booking: Optional[PatientSlotBooking]) -> None:
    if not crt_parent or not slot_booking:
        return
    amount = float(slot_booking.amount or _slot_booking_amount(slot_booking) or 0)
    paid = float(slot_booking.paid_amount or 0)
    package_covered = float(getattr(slot_booking, "package_covered_amount", 0) or 0)
    due = float(getattr(slot_booking, "due_amount", 0) or 0)
    crt_parent.total_amount = amount
    crt_parent.paid_amount = paid
    crt_parent.package_covered_amount = package_covered
    crt_parent.due_amount = due
    crt_parent.fully_paid = bool(getattr(slot_booking, "fully_paid", False) or (amount > 0 and due <= 0))
    if due <= 0 and amount > 0 and package_covered > 0:
        crt_parent.payment_status = "PACKAGE_COVERED"
    elif due <= 0 and amount > 0:
        crt_parent.payment_status = "PAID"
    elif package_covered > 0 or paid > 0:
        crt_parent.payment_status = "PARTIAL"
    else:
        crt_parent.payment_status = "UNPAID"


def _sync_crt_parent_payment_from_ledger(db: Session, crt_parent: Optional[CrtProgramBooking], extra_paid: float = 0) -> None:
    if not crt_parent:
        return
    child_slot_ids = [
        int(row.id)
        for row in (getattr(crt_parent, "slot_bookings", None) or [])
        if getattr(row, "id", None)
    ]
    if not child_slot_ids:
        child_slot_ids = [
            int(row.id)
            for row in db.query(PatientSlotBooking.id)
            .filter(PatientSlotBooking.crt_program_booking_id == crt_parent.id)
            .all()
            if getattr(row, "id", None)
        ]
    ledger_filters = [Payment.crt_program_booking_id == crt_parent.id]
    if child_slot_ids:
        ledger_filters.append(Payment.patient_slot_booking_id.in_(child_slot_ids))
        ledger_filters.extend(Payment.remark.like(f"[slot:{slot_id}]%") for slot_id in child_slot_ids)
    ledger_filter = or_(*ledger_filters)
    marked_fully_paid = (
        db.query(Payment.id)
        .filter(
            ledger_filter,
            Payment.fully_paid.is_(True),
            Payment.due_amount <= 0,
        )
        .first()
        is not None
    )
    ledger_paid = float(
        db.query(func.coalesce(func.sum(Payment.payment_amount), 0))
        .filter(ledger_filter)
        .scalar()
        or 0
    )
    extra_paid = max(0, float(extra_paid or 0))
    if ledger_paid <= 0 and extra_paid <= 0:
        return
    paid = ledger_paid + extra_paid
    total = float(crt_parent.total_amount or 0)
    package_covered = float(crt_parent.package_covered_amount or 0)
    due = 0 if marked_fully_paid else max(0, total - paid - package_covered)
    crt_parent.paid_amount = paid
    crt_parent.due_amount = due
    crt_parent.fully_paid = bool(marked_fully_paid or (total > 0 and due <= 0))
    if due <= 0 and total > 0 and package_covered > 0:
        crt_parent.payment_status = "PACKAGE_COVERED"
    elif due <= 0 and total > 0:
        crt_parent.payment_status = "PAID"
    elif package_covered > 0 or paid > 0:
        crt_parent.payment_status = "PARTIAL"
    else:
        crt_parent.payment_status = "UNPAID"


def _recalculate_slot_payment(slot_booking: PatientSlotBooking) -> None:
    amount = _slot_booking_amount(slot_booking)
    paid = float(slot_booking.paid_amount or 0)
    package = slot_booking.patient_package
    if bool(getattr(slot_booking, "fully_paid", False)):
        _set_slot_payment_from_amounts(slot_booking, amount, paid, 0, amount if slot_booking.is_package_session else 0)
        return
    covered_by_paid_package = bool(slot_booking.is_package_session and package and _is_package_fully_paid(package))
    due = 0 if covered_by_paid_package else max(0, amount - paid)

    _set_slot_payment_from_amounts(slot_booking, amount, paid, due, amount if covered_by_paid_package else 0)


def _recalculate_package_slot_payments(
    db: Session,
    patient_package: Optional[PatientPackage],
    *,
    record_credit_payments: bool = False,
    sync_paid_amount: bool = True,
) -> None:
    if not patient_package:
        return

    if sync_paid_amount:
        _sync_patient_package_paid_amount(patient_package)
    completed_status_id = MASTER_LOOKUP_DATA["patient_slot_booking"]["COMPLETED"]
    all_slot_bookings = (
        db.query(PatientSlotBooking)
        .options(
            joinedload(PatientSlotBooking.patient_package).joinedload(PatientPackage.package),
            joinedload(PatientSlotBooking.program),
            joinedload(PatientSlotBooking.crt_program_booking),
            joinedload(PatientSlotBooking.patient_session_plan_item),
        )
        .filter(
            PatientSlotBooking.patient_package_id == patient_package.id,
            PatientSlotBooking.is_package_session.is_(True),
            PatientSlotBooking.status_id != MASTER_LOOKUP_DATA["patient_slot_booking"]["UNPAID_CANCELLED"],
        )
        .order_by(PatientSlotBooking.created_at.asc(), PatientSlotBooking.id.asc())
        .all()
    )
    slot_bookings: list[PatientSlotBooking] = []
    emitted_crt_keys: set[tuple] = set()
    for slot_booking in all_slot_bookings:
        if slot_booking.crt_program_booking_id:
            mapping = slot_booking.therapist_slot_mapping
            slot = mapping.slot if mapping else None
            crt_key = (
                int(slot_booking.crt_program_booking_id),
                getattr(mapping, "slot_date", None),
                getattr(slot, "start_time", None),
                getattr(slot, "end_time", None),
                0 if mapping and getattr(mapping, "slot_date", None) and slot else int(slot_booking.id or 0),
            )
            if crt_key in emitted_crt_keys:
                continue
            emitted_crt_keys.add(crt_key)
            siblings = [
                row
                for row in all_slot_bookings
                if row.crt_program_booking_id == slot_booking.crt_program_booking_id
                and (
                    int(row.id or 0) == int(slot_booking.id or 0)
                    or (
                        getattr(getattr(row, "therapist_slot_mapping", None), "slot_date", None) == getattr(mapping, "slot_date", None)
                        and getattr(getattr(getattr(row, "therapist_slot_mapping", None), "slot", None), "start_time", None) == getattr(slot, "start_time", None)
                        and getattr(getattr(getattr(row, "therapist_slot_mapping", None), "slot", None), "end_time", None) == getattr(slot, "end_time", None)
                    )
                )
            ]
            payable_slot = max(siblings, key=lambda row: (float(row.amount or _slot_booking_amount(row) or 0), -int(row.id or 0)))
            slot_bookings.append(payable_slot)
        else:
            slot_bookings.append(slot_booking)

    package_credit_remaining = _package_direct_paid_amount(patient_package)
    for slot_booking in slot_bookings:
        crt_parent = slot_booking.crt_program_booking
        amount = float((crt_parent.total_amount if crt_parent else None) or _slot_booking_amount(slot_booking) or 0)
        if slot_booking.status_id != completed_status_id:
            if crt_parent:
                _set_crt_payment_from_amounts(crt_parent, amount, float(crt_parent.paid_amount or 0), float(crt_parent.due_amount or 0), float(crt_parent.package_covered_amount or 0))
            else:
                _set_slot_payment_from_amounts(slot_booking, amount, float(slot_booking.paid_amount or 0), 0, 0)
            continue
        paid = float((crt_parent.paid_amount if crt_parent else slot_booking.paid_amount) or 0)
        marked_fully_paid = bool(getattr(slot_booking, "fully_paid", False)) or bool(getattr(crt_parent, "fully_paid", False)) or _slot_marked_fully_paid(db, slot_booking)
        if marked_fully_paid:
            existing_covered = min(amount, float((crt_parent.package_covered_amount if crt_parent else getattr(slot_booking, "package_covered_amount", 0)) or 0))
            package_credit_remaining = max(0, package_credit_remaining - existing_covered)
            if crt_parent:
                _set_crt_payment_from_amounts(crt_parent, amount, paid, 0, existing_covered)
                crt_parent.fully_paid = True
                crt_parent.payment_status = "PAID"
            else:
                _set_slot_payment_from_amounts(slot_booking, amount, paid, 0, existing_covered)
                slot_booking.fully_paid = True
                slot_booking.payment_status = "PAID"
            continue
        package_applied = min(package_credit_remaining, max(0, amount - paid))
        package_credit_remaining = max(0, package_credit_remaining - package_applied)
        due = max(0, amount - paid - package_applied)
        if crt_parent:
            _set_crt_payment_from_amounts(crt_parent, amount, paid, due, package_applied)
        else:
            _set_slot_payment_from_amounts(slot_booking, amount, paid, due, package_applied)

    _rebuild_patient_package_payment_due_snapshots(patient_package)


def _package_credit_snapshot(patient_package: PatientPackage) -> dict[tuple[str, int], dict[str, Any]]:
    snapshot: dict[tuple[str, int], dict[str, Any]] = {}
    for slot_booking in _package_billable_usage_rows(patient_package):
        crt_parent = slot_booking.crt_program_booking
        key = ("crt", int(slot_booking.crt_program_booking_id)) if slot_booking.crt_program_booking_id else ("slot", int(slot_booking.id))
        snapshot[key] = {
            "slot": slot_booking,
            "covered": float((crt_parent.package_covered_amount if crt_parent else getattr(slot_booking, "package_covered_amount", 0)) or 0),
            "due": float((crt_parent.due_amount if crt_parent else getattr(slot_booking, "due_amount", 0)) or 0),
            "amount": float((crt_parent.total_amount if crt_parent else slot_booking.amount) or _slot_booking_amount(slot_booking) or 0),
        }
    return snapshot


def _record_package_credit_delta_payments(
    db: Session,
    before: dict[tuple[str, int], dict[str, Any]],
    after: dict[tuple[str, int], dict[str, Any]],
    package_payment: Payment,
    user_id: Optional[int],
) -> None:
    running_credit = max(0, float(package_payment.payment_amount or 0))
    for key, after_row in after.items():
        slot_booking = after_row["slot"]
        before_covered = float(before.get(key, {}).get("covered", 0) or 0)
        after_covered = float(after_row.get("covered", 0) or 0)
        if after_covered - before_covered <= 0.01:
            continue
        amount = float(after_row.get("amount", 0) or 0)
        pending_before = max(0, float(before.get(key, {}).get("due", amount) or 0))
        applied = min(running_credit, pending_before)
        running_credit = max(0, running_credit - applied)
        row_due = -running_credit if running_credit > 0 else max(0, pending_before - applied)
        patient_id = slot_booking.patient_id
        if not patient_id and slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
            patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
        if not patient_id and slot_booking.patient_package:
            patient_id = slot_booking.patient_package.patient_id
        if not patient_id:
            continue
        db.add(Payment(
            patient_id=patient_id,
            patient_slot_booking_id=slot_booking.id if not slot_booking.crt_program_booking_id else None,
            crt_program_booking_id=slot_booking.crt_program_booking_id,
            amount=amount,
            payment_amount=0,
            payment_status="PAID",
            fully_paid=row_due <= 0,
            due_amount=row_due,
            payment_date=package_payment.payment_date or _now(),
            remark=f"[slot:{slot_booking.id}] Payment done for Package credit used",
            payment_note=f"Allocated from package payment {package_payment.id}",
            created_by=user_id,
            updated_by=user_id,
        ))


def _transaction_slot_shape(slot_booking: PatientSlotBooking, payment: Optional[Payment] = None) -> dict:
    if not slot_booking.is_package_session:
        _recalculate_slot_payment(slot_booking)
    crt_parent = slot_booking.crt_program_booking
    is_crt_package_session = bool(crt_parent.is_package_session) if crt_parent else bool(slot_booking.is_package_session)
    crt_patient_package = crt_parent.patient_package if crt_parent and crt_parent.patient_package else None
    row_patient_package = crt_patient_package or slot_booking.patient_package
    mapping = slot_booking.therapist_slot_mapping
    slot = mapping.slot if mapping else None
    therapy = mapping.therapy if mapping else None
    start = getattr(slot, "start_time", None)
    end = getattr(slot, "end_time", None)
    slot_date = mapping.slot_date if mapping else None
    session_label = "Session"
    therapy_name = getattr(therapy, "therapy_name", None) or getattr(therapy, "name", None)
    program_name = slot_booking.program.program_name if slot_booking.program else None
    if program_name and therapy_name:
        session_label = f"{program_name} - {therapy_name}"
    elif program_name:
        session_label = program_name
    elif therapy_name:
        session_label = therapy_name
    if start and end:
        session_label = f"{session_label} · {start}-{end}"
    if slot_date:
        session_label = f"{session_label} · {slot_date.strftime('%d/%m/%Y')}"

    return {
        "id": slot_booking.id,
        "transaction_type": "Package Session" if is_crt_package_session else "Session Due",
        "patient_slot_booking_id": slot_booking.id,
        "crt_program_booking_id": slot_booking.crt_program_booking_id,
        "group_program_booking_id": getattr(slot_booking, "group_program_booking_id", None),
        "patient_id": crt_parent.patient_id if crt_parent else slot_booking.patient_id,
        "patient_package_id": crt_parent.patient_package_id if crt_parent else slot_booking.patient_package_id,
        "package_name": row_patient_package.package.name if row_patient_package and row_patient_package.package else None,
        "is_package_session": is_crt_package_session,
        "slot_name": getattr(slot, "slot_name", None),
        "session_name": session_label,
        "therapy_name": therapy_name,
        "program_name": program_name,
        "program_id": slot_booking.program_id,
        "slot_date": _iso(slot_date),
        "start_time": _iso(start),
        "end_time": _iso(end),
        "amount": float(crt_parent.total_amount if crt_parent else (_slot_booking_amount(slot_booking) or 0)),
        "paid_amount": float(crt_parent.paid_amount if crt_parent else (slot_booking.paid_amount or 0)),
        "package_covered_amount": float((crt_parent.package_covered_amount if crt_parent else getattr(slot_booking, "package_covered_amount", 0)) or 0),
        "due_amount": float(crt_parent.due_amount if crt_parent else (slot_booking.due_amount or 0)),
        "fully_paid": bool((crt_parent.fully_paid if crt_parent else getattr(slot_booking, "fully_paid", False))),
        "payment_status": crt_parent.payment_status if crt_parent else slot_booking.payment_status,
        "payment_mode": payment.payment_mode_master.payment_mode_name if payment and payment.payment_mode_master else None,
        "payment_date": _iso(payment.payment_date if payment else None),
        "status": slot_booking.status,
        "created_at": _iso(slot_booking.created_at),
    }


def _crt_parent_transaction_shape(crt_parent: CrtProgramBooking, payment: Optional[Payment] = None) -> dict:
    program_name = crt_parent.program.program_name if crt_parent.program else "CRT"
    slot = crt_parent.slot
    start = getattr(slot, "start_time", None)
    end = getattr(slot, "end_time", None)
    session_label = program_name or "CRT"
    if start and end:
        session_label = f"{session_label} Â· {start}-{end}"
    if crt_parent.slot_date:
        session_label = f"{session_label} Â· {crt_parent.slot_date.strftime('%d/%m/%Y')}"
    row_patient_package = crt_parent.patient_package
    return {
        "id": f"crt-{crt_parent.id}",
        "transaction_type": "Package Session" if crt_parent.is_package_session else "Session Due",
        "patient_slot_booking_id": None,
        "crt_program_booking_id": crt_parent.id,
        "group_program_booking_id": None,
        "patient_id": crt_parent.patient_id,
        "patient_package_id": crt_parent.patient_package_id,
        "package_name": row_patient_package.package.name if row_patient_package and row_patient_package.package else None,
        "is_package_session": bool(crt_parent.is_package_session or crt_parent.patient_package_id),
        "slot_name": getattr(slot, "slot_name", None),
        "session_name": session_label,
        "therapy_name": None,
        "program_name": program_name,
        "program_id": crt_parent.program_id,
        "slot_date": _iso(crt_parent.slot_date),
        "start_time": _iso(start),
        "end_time": _iso(end),
        "amount": float(crt_parent.total_amount or 0),
        "paid_amount": float(crt_parent.paid_amount or 0),
        "package_covered_amount": float(crt_parent.package_covered_amount or 0),
        "due_amount": float(crt_parent.due_amount or 0),
        "fully_paid": bool(crt_parent.fully_paid),
        "payment_status": crt_parent.payment_status,
        "payment_mode": payment.payment_mode_master.payment_mode_name if payment and payment.payment_mode_master else None,
        "payment_date": _iso(payment.payment_date if payment else None),
        "status": crt_parent.payment_status,
        "created_at": _iso(crt_parent.created_at),
    }


def _package_purchase_transaction_shape(patient_package: PatientPackage, initial_paid_amount: Optional[float] = None) -> dict:
    paid_amount = max(0, float(initial_paid_amount if initial_paid_amount is not None else _package_effective_paid_amount(patient_package)))
    package_label = patient_package.package.name if patient_package.package else f"Package #{patient_package.package_id}"
    return {
        "id": f"package-{patient_package.id}",
        "transaction_type": "Package Purchase",
        "patient_slot_booking_id": None,
        "patient_id": patient_package.patient_id,
        "patient_package_id": patient_package.id,
        "package_name": package_label,
        "is_package_session": False,
        "slot_name": None,
        "session_name": f"{package_label} purchased",
        "therapy_name": None,
        "slot_date": _iso(patient_package.start_date),
        "start_time": None,
        "end_time": None,
        "amount": float(patient_package.total_amount or (patient_package.package.price if patient_package.package else 0) or 0),
        "paid_amount": 0,
        "due_amount": -paid_amount,
        "payment_status": patient_package.payment_status,
        "payment_mode": "Package",
        "payment_date": _iso(patient_package.created_at),
        "status": patient_package.status,
        "created_at": _iso(patient_package.created_at),
    }


def _payment_transaction_shape(
    payment: Payment,
    *,
    slot_booking: Optional[PatientSlotBooking] = None,
    patient_package: Optional[PatientPackage] = None,
    cumulative_paid: float = 0,
    cumulative_package_paid: Optional[float] = None,
    starting_due: Optional[float] = None,
    previous_due: Optional[float] = None,
    display_amount_override: Optional[float] = None,
    due_override: Optional[float] = None,
) -> dict:
    payment_amount = float(payment.payment_amount or 0)
    stored_amount = float(getattr(payment, "amount", 0) or 0)
    clean_remark = re.sub(r"^\[[^\]]+\]\s*", "", payment.remark or "").strip()
    assessment_source_id = None
    assessment_payment_match = re.match(r"^\[assessment-payment:(\d+)\]", payment.remark or "", flags=re.I)
    assessment_match = re.match(r"^\[assessment:(\d+)\]", payment.remark or "", flags=re.I)
    if assessment_payment_match:
        assessment_source_id = int(assessment_payment_match.group(1))
    elif assessment_match:
        assessment_source_id = payment.id
    assessment_source_id = int(getattr(payment, "assessment_source_payment_id", None) or assessment_source_id or 0) or None
    assessment_billing = None
    assessment_source_payment = None
    if assessment_source_id:
        db = object_session(payment)
        if db:
            assessment_source_payment = (
                db.query(Payment)
                .filter(Payment.id == assessment_source_id)
                .first()
            )
            assessment_billing = (
                db.query(PatientAssessmentBilling)
                .filter(
                    PatientAssessmentBilling.patient_id == payment.patient_id,
                    PatientAssessmentBilling.source_payment_id == assessment_source_id,
                )
                .first()
            )
    payment_session_text = clean_remark
    payment_note = str(getattr(payment, "payment_note", "") or "").strip()
    if " | Note: " in clean_remark:
        payment_session_text, legacy_note = clean_remark.split(" | Note: ", 1)
        payment_session_text = payment_session_text.strip()
        payment_note = payment_note or legacy_note.strip()
    while re.match(r"^Payment\s+(?:done\s+)?for\s+Payment\s+(?:done\s+)?for\s+", payment_session_text, flags=re.I):
        payment_session_text = re.sub(r"^Payment\s+(?:done\s+)?for\s+", "", payment_session_text, count=1, flags=re.I).strip()
    if not re.match(r"^Payment\s+(?:done\s+)?for\b", payment_session_text, flags=re.I):
        payment_session_text = f"Payment done for {payment_session_text}" if payment_session_text else ""
    if slot_booking:
        base_row = _transaction_slot_shape(slot_booking, payment)
        slot_fully_paid = bool(getattr(payment, "fully_paid", False))
        payment_due = float(getattr(payment, "due_amount", 0) or 0)
        crt_parent = slot_booking.crt_program_booking
        row_patient_package = (crt_parent.patient_package if crt_parent and crt_parent.patient_package else None) or slot_booking.patient_package
        is_package_session = bool(getattr(slot_booking, "is_package_session", False)) or bool(getattr(crt_parent, "is_package_session", False))
        is_package_credit_row = is_package_session and "Package credit used" in str(payment.remark or "")
        display_session_name = base_row.get("session_name") or payment_session_text
        due = payment_due
        if display_amount_override is not None:
            display_amount = float(display_amount_override)
        elif is_package_credit_row:
            display_amount = stored_amount if stored_amount > 0 else _slot_booking_amount(slot_booking)
        elif "Session completed" in str(payment.remark or "") and payment_amount <= 0:
            display_amount = stored_amount if stored_amount > 0 else _slot_booking_amount(slot_booking)
        else:
            display_amount = stored_amount if stored_amount > 0 else _slot_booking_amount(slot_booking)
        return {
            **base_row,
            "id": f"payment-{payment.id}",
            "transaction_type": "Payment",
            "crt_program_booking_id": getattr(payment, "crt_program_booking_id", None) or base_row.get("crt_program_booking_id"),
            "amount": display_amount,
            "paid_amount": payment_amount,
            "due_amount": due,
            "fully_paid": slot_fully_paid,
            "session_name": display_session_name,
            "payment_status": payment.payment_status or "PAID",
            "payment_mode": payment.payment_mode_master.payment_mode_name if payment.payment_mode_master else None,
            "transaction_id": getattr(payment, "transaction_id", None),
            "payment_remark": payment_note,
            "payment_date": _iso(payment.payment_date or payment.created_at),
            "created_at": _iso(payment.created_at),
        }

    if patient_package:
        due_amount = float(getattr(payment, "due_amount", 0) or 0)
        package_label = patient_package.package.name if patient_package.package else f"Package #{patient_package.package_id}"
        payment_note_lower = str(getattr(payment, "payment_note", "") or "").strip().lower()
        package_session_name = (
            f"{package_label} purchased"
            if payment_note_lower == "package purchased"
            else "Package credit added"
        )
        is_package_purchase_row = payment_note_lower == "package purchased"
        amount = stored_amount if is_package_purchase_row and stored_amount > 0 else 0
        db = object_session(patient_package) or object_session(payment)
        if not payment_note_lower and not is_package_purchase_row and db and patient_package.id:
            first_package_payment = (
                db.query(Payment.id)
                .filter(
                    Payment.patient_id == patient_package.patient_id,
                    Payment.payment_amount > 0,
                    Payment.remark.like(f"[package:{patient_package.id}]%"),
                )
                .order_by(Payment.payment_date.asc(), Payment.created_at.asc(), Payment.id.asc())
                .first()
            )
            if first_package_payment and first_package_payment.id == payment.id:
                package_session_name = f"{package_label} purchased"
                is_package_purchase_row = True
                amount = stored_amount if stored_amount > 0 else float(patient_package.total_amount or 0)
        return {
            "id": f"payment-{payment.id}",
            "transaction_type": "Payment",
            "patient_slot_booking_id": None,
            "patient_id": payment.patient_id,
            "patient_package_id": patient_package.id,
            "package_name": package_label,
            "is_package_session": False,
            "slot_name": None,
            "session_name": package_session_name,
            "therapy_name": None,
            "slot_date": _iso(patient_package.start_date),
            "start_time": None,
            "end_time": None,
            "amount": amount,
            "paid_amount": payment_amount,
            "due_amount": due_amount,
            "fully_paid": bool(getattr(payment, "fully_paid", False)),
            "payment_status": payment.payment_status or "PAID",
            "payment_mode": payment.payment_mode_master.payment_mode_name if payment.payment_mode_master else None,
            "transaction_id": getattr(payment, "transaction_id", None),
            "payment_remark": payment_note,
            "payment_date": _iso(payment.payment_date or payment.created_at),
            "status": patient_package.status,
            "created_at": _iso(payment.created_at),
        }

    is_assessment_due = bool(assessment_source_id and payment_amount <= 0 and float(getattr(payment, "due_amount", 0) or 0) > 0)
    assessment_display_amount = float(getattr(payment, "amount", 0) or 0)
    if assessment_source_id:
        assessment_display_amount = float(getattr(assessment_billing, "assessment_amount", 0) or 0)
        if assessment_display_amount <= 0 and assessment_source_payment:
            assessment_display_amount = float(getattr(assessment_source_payment, "amount", 0) or 0)
        if assessment_display_amount <= 0 and assessment_source_payment:
            assessment_display_amount = (
                float(getattr(assessment_source_payment, "payment_amount", 0) or 0)
                + float(getattr(assessment_source_payment, "due_amount", 0) or 0)
            )
        if assessment_display_amount <= 0:
            assessment_display_amount = stored_amount if stored_amount > 0 else payment_amount + float(getattr(payment, "due_amount", 0) or 0)
    return {
        "id": f"payment-{payment.id}",
        "transaction_type": "Assessment Due" if is_assessment_due else "Payment",
        "patient_slot_booking_id": None,
        "patient_id": payment.patient_id,
        "patient_package_id": None,
        "assessment_payment_id": assessment_source_id or payment.id,
        "assessment_source_payment_id": assessment_source_id or payment.id,
        "is_package_session": False,
        "slot_name": None,
        "session_name": payment_session_text.replace("Assessment completed - ", "").replace("Payment done for ", "").strip() or "Assessment",
        "therapy_name": None,
        "slot_date": None,
        "start_time": None,
        "end_time": None,
        "amount": assessment_display_amount,
        "paid_amount": payment_amount,
        "due_amount": float(getattr(payment, "due_amount", 0) or 0),
        "fully_paid": bool(getattr(payment, "fully_paid", False)),
        "payment_status": payment.payment_status or "PAID",
        "payment_mode": payment.payment_mode_master.payment_mode_name if payment.payment_mode_master else None,
        "transaction_id": getattr(payment, "transaction_id", None),
        "payment_remark": payment_note,
        "payment_date": _iso(payment.payment_date or payment.created_at),
        "status": payment.payment_status or "PAID",
        "created_at": _iso(payment.created_at),
    }


def _assessment_billing_transaction_shape(billing: PatientAssessmentBilling) -> dict:
    assessment_name = (
        billing.assessment.assessment_name
        if billing.assessment and billing.assessment.assessment_name
        else billing.assessment.title
        if billing.assessment
        else f"Assessment #{billing.assessment_id}"
    )
    amount = float(billing.assessment_amount or 0)
    paid = float(billing.paid_amount or 0)
    due = max(0, float(billing.due_amount or 0))
    source_payment = getattr(billing, "source_payment", None)
    ledger_at = (
        getattr(source_payment, "payment_date", None)
        or getattr(source_payment, "created_at", None)
        or billing.completed_at
        or billing.created_at
    )
    return {
        "id": f"assessment-billing-{billing.id}",
        "transaction_type": "Assessment Due",
        "patient_slot_booking_id": None,
        "patient_id": billing.patient_id,
        "patient_package_id": None,
        "assessment_billing_id": billing.id,
        "assessment_payment_id": billing.source_payment_id,
        "assessment_source_payment_id": billing.source_payment_id,
        "is_package_session": False,
        "slot_name": None,
        "session_name": assessment_name,
        "therapy_name": None,
        "slot_date": _iso(billing.completed_at),
        "start_time": None,
        "end_time": None,
        "amount": amount,
        "paid_amount": 0,
        "due_amount": amount,
        "fully_paid": False,
        "current_due_amount": due,
        "current_fully_paid": bool(getattr(billing, "fully_paid", False)),
        "payment_status": billing.payment_status,
        "payment_mode": None,
        "transaction_id": None,
        "payment_remark": None,
        "payment_date": _iso(ledger_at),
        "status": billing.payment_status,
        "created_at": _iso(ledger_at),
    }


def _payment_mode_id(db: Session, value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        mode = db.query(PaymentModeMaster).filter(PaymentModeMaster.id == value).first()
        if not mode:
            return None
        normalized = str(mode.payment_mode_name or "").strip().lower().replace(" ", "")
        if normalized not in {"cash", "gpay", "googlepay"}:
            raise HTTPException(status_code=400, detail="Payment mode must be Cash or GPay")
        return value
    if isinstance(value, str) and value.isdigit():
        return _payment_mode_id(db, int(value))

    mode_name = str(value).strip()
    normalized = mode_name.lower().replace(" ", "")
    if normalized not in {"cash", "gpay", "googlepay"}:
        raise HTTPException(status_code=400, detail="Payment mode must be Cash or GPay")
    if normalized == "googlepay":
        mode_name = "GPay"
    mode = db.query(PaymentModeMaster).filter(PaymentModeMaster.payment_mode_name == mode_name).first()
    if mode:
        return mode.id

    mode = PaymentModeMaster(payment_mode_name=mode_name)
    db.add(mode)
    db.flush()
    return mode.id


def _alert_shape(alert: Any) -> dict:
    severity = {"low": "info", "medium": "warning", "high": "warning"}.get(alert.severity, alert.severity)
    return {
        "id": alert.id,
        "alert_type": alert.alert_type,
        "severity": severity,
        "title": alert.title,
        "message": alert.description,
        "description": alert.description,
        "entity_type": (alert.metadata_json or {}).get("entity_type") if alert.metadata_json else None,
        "entity_id": (alert.metadata_json or {}).get("entity_id") if alert.metadata_json else None,
        "is_resolved": not alert.is_active,
        "is_active": alert.is_active,
        "resolved_by": None,
        "resolved_at": None,
        "created_at": _iso(alert.created_at),
        "updated_at": _iso(alert.updated_at),
        "created_by": None,
        "updated_by": None,
    }


def _waitlist_shape(entry: Any) -> dict:
    patient = entry.patient
    therapist = entry.preferred_therapist
    return {
        "id": entry.id,
        "patient_id": entry.patient_id,
        "patientName": f"{patient.first_name} {patient.last_name}".strip() if patient else "Unknown",
        "patientCode": f"PT-{entry.patient_id:04d}",
        "requestedService": entry.requested_service,
        "preferredTherapist": therapist.name if therapist else None,
        "priority": entry.priority,
        "preferredDays": entry.preferred_days or [],
        "preferredTime": entry.preferred_time,
        "notes": entry.notes,
        "status": entry.status,
        "requestedAt": _iso(entry.requested_at),
        "created_at": _iso(entry.created_at),
        "updated_at": _iso(entry.updated_at),
    }


def _document_shape(document: Document) -> dict:
    db = object_session(document)
    doc_type = _document_type_label(db, document.document_type_id)
    file_url = f"api/v1/ui/documents/{document.id}/view"
    return {
        "id": document.id,
        "patient_id": document.patient_id,
        "session_id": None,
        "uploaded_by": document.uploaded_by,
        "doc_type": doc_type,
        "document_type": doc_type,
        "title": document.title,
        "file_url": file_url,
        "view_url": file_url,
        "download_url": f"api/v1/ui/documents/{document.id}/download",
        "file_path": document.file_path,
        "file_name": document.title or (document.file_path.rsplit("/", 1)[-1] if document.file_path else None),
        "file_size": document.file_size,
        "mime_type": None,
        "notes": document.description,
        "description": document.description,
        "created_at": _iso(document.created_at),
        "updated_at": _iso(document.updated_at),
        "deleted_at": None,
        "patients": _patient_shape(document.patient) if document.patient else None,
    }


def _audit_shape(log: AuditLog) -> dict:
    return {
        "id": log.id,
        "table_name": log.entity_type,
        "entity_type": log.entity_type,
        "record_id": log.entity_id,
        "entity_id": log.entity_id,
        "action": log.action,
        "old_data": log.old_values,
        "new_data": log.new_values,
        "changed_by": log.user_id,
        "changed_at": _iso(log.created_at),
        "created_at": _iso(log.created_at),
        "ip_address": log.ip_address,
        "user_agent": log.user_agent,
    }


def _note_shape(note: Any) -> dict:
    therapist = None
    if note.session and note.session.therapist:
        therapist = note.session.therapist
    return {
        "id": note.id,
        "session_id": note.session_id,
        "therapist_id": therapist.id if therapist else None,
        "note": note.content,
        "content": note.content,
        "note_type": note.note_type,
        "created_at": _iso(note.created_at),
        "created_by": note.created_by,
        "therapists": _therapist_shape(therapist) if therapist else None,
    }


def _assignment_shape(assignment: Any) -> dict:
    therapist = assignment.session.therapist if assignment.session else None
    return {
        "id": assignment.id,
        "session_id": assignment.session_id,
        "therapist_id": therapist.id if therapist else None,
        "assigned_at": _iso(assignment.created_at),
        "unassigned_at": _iso(assignment.deleted_at),
        "reason": None,
        "therapists": _therapist_shape(therapist) if therapist else None,
    }

def _therapist_therapy_mapping_shape(mapping: TherapistTherapyMapping) -> dict:
    return {
        "id": mapping.id,
        "therapist_id": mapping.therapist_id,
        "therapy_id": mapping.therapy_id,
        "is_active": bool(mapping.is_active),
        "therapy": {
            "id": mapping.therapy.id,
            "therapy_id": mapping.therapy.id,
            "therapy_name": mapping.therapy.name,
            "name": mapping.therapy.name,
        } if mapping.therapy else None,
    }


SHAPERS = {
    "users": _user_shape_from_model,
    "roles": _role_shape,
    "user_roles": _user_role_shape,
    "patients": _patient_shape,
    "enquiries": _enquiry_shape,
    "therapists": _therapist_shape,
    "therapist_availability": _availability_shape,
    "therapist_leaves": _therapist_leave_shape,
    "appointments": _appointment_shape,
    "sessions": _session_shape,
    "packages": _master_package_shape,
    "patient_packages": _package_shape,
    "invoices": _invoice_shape,
    "payments": _payment_shape,
    "alerts": _alert_shape,
    "waitlist_entries": _waitlist_shape,
    "documents": _document_shape,
    "audit_logs": _audit_shape,
    "session_notes": _note_shape,
    "session_assignments": _assignment_shape,
    "therapist_therapy_mapping": _therapist_therapy_mapping_shape,
}


ALLOWED_UI_TABLES = {
    "users",
    "roles",
    "user_roles",
    "patients",
    "enquiries",
    "therapists",
    "therapist_availability",
    "therapist_leaves",
    "appointments",
    "sessions",
    "packages",
    "patient_packages",
    "invoices",
    "payments",
    "alerts",
    "waitlist_entries",
    "documents",
    "audit_logs",
    "session_notes",
    "session_assignments",
    "patient_duplicates",
    "patients_history",
    "therapist_therapy_mapping",
}


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _parse_time(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    return time.fromisoformat(value[:5])


def _base_query(table: str, db: Session, user: Optional[User]):
    if table not in ALLOWED_UI_TABLES:
        raise HTTPException(status_code=404, detail=f"Unsupported table: {table}")
    if table in REMOVED_DB_TABLES:
        if table == "session_assignments":
            return db.query(TherapySession).filter(False)
        raise HTTPException(status_code=404, detail=f"Table is not present in the database: {table}")

    region_ids = _user_region_ids(db, user)
    roles = _user_roles(db, user)
    current_therapist = _current_therapist(db, user) if _is_therapist_scoped_role(roles) else None
    if table == "patients":
        query = db.query(Patient)
        if region_ids:
            query = query.filter(Patient.region_id.in_(region_ids))
        return query
    if table == "enquiries":
        query = db.query(Enquiry).options(joinedload(Enquiry.program)).filter(Enquiry.is_active.is_(True))
        if region_ids:
            query = query.filter(Enquiry.region_id.in_(region_ids))
        return query
    if table == "users":
        query = db.query(User).filter(User.deleted_at.is_(None))
        if region_ids:
            mapped_user_ids = db.query(UserRegionMapping.userid).filter(UserRegionMapping.regionid.in_(region_ids))
            query = query.filter(User.id.in_(mapped_user_ids))
        return query
    if table == "roles":
        return db.query(Role).filter(Role.deleted_at.is_(None))
    if table == "user_roles":
        return db.query(UserRole).filter(UserRole.deleted_at.is_(None))
    if table == "therapists":
        query = db.query(Therapist).options(
            joinedload(Therapist.user),
            joinedload(Therapist.therapy_mappings).joinedload(TherapistTherapyMapping.therapy),
        )
        if current_therapist:
            query = query.filter(Therapist.id == current_therapist.id)
        return query
    if table == "therapist_availability":
        query = db.query(TherapistAvailability).options(joinedload(TherapistAvailability.therapist)).filter(TherapistAvailability.deleted_at.is_(None))
        if current_therapist:
            query = query.filter(TherapistAvailability.therapist_id == current_therapist.id)
        elif region_ids:
            query = query.join(Therapist, Therapist.id == TherapistAvailability.therapist_id).filter(Therapist.region_id.in_(region_ids))
        return query
    if table == "therapist_leaves":
        query = db.query(TherapistLeave).options(joinedload(TherapistLeave.therapist))
        if current_therapist:
            query = query.filter(TherapistLeave.therapist_id == current_therapist.id)
        elif region_ids:
            query = query.join(Therapist, Therapist.id == TherapistLeave.therapist_id).filter(Therapist.region_id.in_(region_ids))
        return query
    if table == "appointments":
        query = (
            db.query(Appointment)
            .options(joinedload(Appointment.patient), joinedload(Appointment.therapist))
            .filter(Appointment.deleted_at.is_(None))
        )
        if current_therapist:
            query = query.filter(Appointment.therapist_id == current_therapist.id)
        elif region_ids:
            query = query.filter(Appointment.region_id.in_(region_ids))
        return query
    if table == "sessions":
        query = (
            db.query(TherapySession)
            .options(joinedload(TherapySession.patient), joinedload(TherapySession.therapist))
            .filter(TherapySession.deleted_at.is_(None))
        )
        if current_therapist:
            query = query.filter(TherapySession.therapist_id == current_therapist.id)
        elif region_ids:
            query = query.join(Patient, Patient.id == TherapySession.patient_id).filter(Patient.region_id.in_(region_ids))
        return query
    if table == "packages":
        allowed_package_names = (
            "General 16 Session - Package",
            "General 12 Session - Package",
            "CRT 5 Session - Package",
            "Vocational 8 Session - Package",
        )
        return db.query(Package).options(joinedload(Package.program)).filter(
            Package.deleted_at.is_(None),
            Package.is_active.is_(True),
            Package.name.in_(allowed_package_names),
        )
    if table == "patient_packages":
        query = db.query(PatientPackage).options(joinedload(PatientPackage.package)).filter(PatientPackage.deleted_at.is_(None))
        if region_ids:
            query = query.join(Patient, Patient.id == PatientPackage.patient_id).filter(Patient.region_id.in_(region_ids))
        return query
    if table == "invoices":
        query = db.query(Invoice).options(joinedload(Invoice.patient)).filter(Invoice.deleted_at.is_(None))
        if region_ids:
            query = query.filter(Invoice.region_id.in_(region_ids))
        return query
    if table == "payments":
        query = db.query(Payment).options(joinedload(Payment.patient))
        if region_ids:
            query = query.join(Patient, Patient.id == Payment.patient_id).filter(Patient.region_id.in_(region_ids))
        return query
    if table == "documents":
        query = db.query(Document).options(joinedload(Document.patient))
        if region_ids:
            query = query.join(Patient, Patient.id == Document.patient_id).filter(Patient.region_id.in_(region_ids))
        return query
    if table == "audit_logs":
        return db.query(AuditLog)
    raise HTTPException(status_code=404, detail=f"Unsupported table: {table}")


FIELD_MAP = {
    "full_name": None,
    "patient_code": None,
    "scheduled_at": None,
    "is_active": None,
    "is_resolved": None,
    "changed_at": AuditLog.created_at,
    "table_name": AuditLog.entity_type,
    "record_id": AuditLog.entity_id,
    "payment_date": Payment.payment_date,
    "leave_date": TherapistLeave.leave_date,
}


def _column_for(table: str, field: str):
    if table == "appointments" and field == "scheduled_at":
        return Appointment.start_time
    if table == "sessions" and field == "is_billed":
        return None
    if field in FIELD_MAP:
        return FIELD_MAP[field]
    model_map = {
        "appointments": Appointment,
        "sessions": TherapySession,
        "patients": Patient,
        "enquiries": Enquiry,
        "therapists": Therapist,
        "therapist_therapy_mapping": TherapistTherapyMapping,
        "therapist_availability": TherapistAvailability,
        "therapist_leaves": TherapistLeave,
        "packages": Package,
        "patient_packages": PatientPackage,
        "invoices": Invoice,
        "payments": Payment,
        "documents": Document,
    }
    if table not in model_map:
        raise HTTPException(status_code=404, detail=f"Unsupported table: {table}")
    return getattr(model_map[table], field, None)


def _apply_filters(query, table: str, filters: list[dict]):
    for f in filters:
        field, op, value = f.get("field"), f.get("op"), f.get("value")
        col = _column_for(table, field)
        if field == "full_name" and table == "patients":
            expression = func.trim(func.concat(
                func.coalesce(Patient.first_name, ""),
                " ",
                func.coalesce(Patient.last_name, ""),
            ))
            if op == "ilike":
                query = query.filter(expression.ilike(value.replace("*", "%")))
            elif op == "eq":
                query = query.filter(expression == value)
        elif field in {"full_name", "first_name"} and table == "enquiries":
            if op == "ilike":
                query = query.filter(Enquiry.first_name.ilike(value.replace("*", "%")))
            elif op == "eq":
                query = query.filter(Enquiry.first_name == value)
        elif field == "is_active" and table == "therapists":
            if op == "eq":
                query = query.filter(Therapist.is_active == (str(value).lower() == "true"))
        elif field == "region_id" and table == "therapists":
            if op == "eq":
                query = query.filter(Therapist.region_id == value)
                continue
        elif table == "therapist_therapy_mapping" and field in {"id", "therapist_id", "therapy_id"}:
            col = getattr(TherapistTherapyMapping, field)
            if op == "eq":
                query = query.filter(col == value)
            elif op == "in":
                query = query.filter(col.in_(value or []))
        elif field == "is_active" and table == "therapist_therapy_mapping":
            if op == "eq":
                query = query.filter(TherapistTherapyMapping.is_active == (str(value).lower() == "true"))
        elif field == "is_resolved" and table == "alerts":
            if op == "eq":
                query = query.filter(Alert.is_active == (str(value).lower() == "false"))
        elif field == "is_billed" and table == "sessions":
            if op == "eq":
                query = query.filter(TherapySession.billing_status.in_(["billed", "paid"]) if str(value).lower() == "true" else TherapySession.billing_status.notin_(["billed", "paid"]))
        elif field == "deleted_at" and table == "documents":
            continue
        elif col is not None:
            if op == "eq":
                query = query.filter(col == value)
            elif op == "in":
                query = query.filter(col.in_(value or []))
            elif op == "gte":
                query = query.filter(col >= value)
            elif op == "lte":
                query = query.filter(col <= value)
            elif op == "lt":
                query = query.filter(col < value)
            elif op == "is" and value is None:
                query = query.filter(col.is_(None))
            elif op == "ilike":
                query = query.filter(col.ilike(value.replace("*", "%")))
        elif op == "is" and value is None:
            continue
    return query


def _apply_order(query, table: str, order_by: Optional[str], ascending: bool):
    if not order_by:
        return query
    if table == "patients" and order_by == "full_name":
        full_name = func.trim(func.concat(
            func.coalesce(Patient.first_name, ""),
            " ",
            func.coalesce(Patient.last_name, ""),
        ))
        return query.order_by(full_name.asc() if ascending else full_name.desc(), Patient.id.asc())
    if table == "therapists" and order_by == "full_name":
        query = query.outerjoin(User, User.id == Therapist.user_id)
        user_name = func.trim(func.concat(
            func.coalesce(User.first_name, ""),
            " ",
            func.coalesce(User.last_name, ""),
        ))
        col = func.coalesce(func.nullif(user_name, ""), Therapist.name)
        return query.order_by(col.asc() if ascending else col.desc(), Therapist.id.asc())
    col = _column_for(table, order_by)
    if col is None:
        return query
    return query.order_by(col.asc() if ascending else col.desc())

@router.post("/auth/login")
async def login(payload: dict, db: Session = Depends(get_db)):
    email = payload.get("email")
    password = payload.get("password")

    user = UserService.authenticate_user(db, email, password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    tokens = AuthService.create_tokens(user, db)
    AuthService.update_last_login(db, user.id)

    return {
        "data": {
            "session": tokens,
            "user": _user_shape(user, db)
        }
    }

@router.post("/auth/refresh")
async def refresh_session(payload: dict, db: Session = Depends(get_db)):
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Refresh token is required")

    token_payload = decode_token(refresh_token)
    if not token_payload or token_payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user_id = token_payload.get("user_id")
    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    tokens = AuthService.create_tokens(user, db)
    return {"data": {"session": tokens, "user": _user_shape(user, db)}}


@router.post("/auth/register")
async def register(payload: dict, db: Session = Depends(get_db)):
    email = payload.get("email")
    password = payload.get("password")
    full_name = payload.get("full_name") or email
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    if UserService.get_user_by_email(db, email):
        raise HTTPException(status_code=400, detail="Email already registered")
    first_name, last_name = _split_name(full_name)
    region = _default_region(db)
    region_id = payload.get("region_id") or region.id
    user = User(
        username=email,
        email=email,
        hashed_password=hash_password(password),
        first_name=first_name,
        last_name=last_name,
        region_id=region_id,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    db.add(UserRegionMapping(userid=user.id, regionid=region_id, created_by=user.id, updated_by=user.id))
    db.commit()
    db.refresh(user)
    return {"data": {"user": _user_shape(user, db)}}


@router.get("/auth/me")
async def me(request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    return {"data": {"user": _user_shape(user, db)}}


@router.post("/auth/change-password")
async def change_password(payload: dict, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    new_password = payload.get("new_password")

    if not new_password:
        raise HTTPException(status_code=400, detail="New password is required")
    password_error = _change_password_policy_error(new_password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)
    if verify_password(new_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="New password must be different from current password")

    user.hashed_password = hash_password(new_password)
    user.updated_at = _now()
    _write_audit_log(
        db,
        user_id=user.id,
        entity_type="users",
        entity_id=user.id,
        action="PASSWORD_CHANGE",
        old_values={"password": "previous_hash"},
        new_values={"password": "updated_hash"},
        request=request,
    )
    db.commit()
    return {"data": {"message": "Password changed successfully"}}


@router.post("/auth/forgot-password")
async def forgot_password(payload: dict, request: Request, db: Session = Depends(get_db)):
    email = payload.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    user = UserService.get_user_by_email(db, email)
    reset_url = None
    email_sent = False

    if user and user.is_active:
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.is_active.is_(True),
        ).update({"is_active": False})

        raw_token = secrets.token_urlsafe(32)
        reset_url = _password_reset_url(raw_token)
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=_token_hash(raw_token),
            is_active=True,
            expires_at=_now() + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.add(reset_token)
        db.flush()
        _write_audit_log(
            db,
            user_id=user.id,
            entity_type="users",
            entity_id=user.id,
            action="PASSWORD_RESET_REQUEST",
            new_values={"expires_in_minutes": settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES},
            request=request,
        )

        try:
            email_sent = _send_password_reset_email(user.email, reset_url)
        except Exception:
            email_sent = False

    db.commit()

    response = {"message": "If the email exists, a password reset link has been generated"}
    if reset_url and not email_sent:
        response["reset_url"] = reset_url
        response["email_sent"] = False
    elif reset_url:
        response["email_sent"] = True

    return {"data": response}


@router.post("/auth/reset-password")
async def reset_password(payload: dict, request: Request, db: Session = Depends(get_db)):
    token = payload.get("token")
    new_password = payload.get("new_password")

    if not token or not new_password:
        raise HTTPException(status_code=400, detail="Token and new password are required")
    password_error = _password_policy_error(new_password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)

    reset_token = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token_hash == _token_hash(token),
            PasswordResetToken.is_active.is_(True),
            PasswordResetToken.expires_at > _now(),
        )
        .first()
    )
    if not reset_token:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = db.query(User).filter(User.id == reset_token.user_id, User.deleted_at.is_(None)).first()
    if not user or not user.is_active:
        reset_token.is_active = False
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.hashed_password = hash_password(new_password)
    user.updated_at = _now()
    reset_token.is_active = False
    reset_token.used_at = _now()
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.id != reset_token.id,
        PasswordResetToken.is_active.is_(True),
    ).update({"is_active": False})
    _write_audit_log(
        db,
        user_id=user.id,
        entity_type="users",
        entity_id=user.id,
        action="PASSWORD_RESET",
        old_values={"password": "previous_hash"},
        new_values={"password": "updated_hash", "reset_token_id": reset_token.id},
        request=request,
    )
    db.commit()
    return {"data": {"message": "Password reset successfully"}}


def _month_key(value: date | datetime) -> str:
    return f"{value.year}-{value.month:02d}"


def _month_label(value: date) -> str:
    return value.strftime("%b")


def _date_range(start: date, end: date) -> list[date]:
    days = max(0, (end - start).days)
    return [start + timedelta(days=offset) for offset in range(days + 1)]


def _analytics_period_window(raw_period: Optional[str]) -> dict:
    today = date.today()
    key = (raw_period or "month").lower()
    if key not in {"today", "week", "month"}:
        raise HTTPException(status_code=400, detail="Unsupported analytics period")

    if key == "today":
        start = today
        end = today
        label = "Today"
    elif key == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        label = "This Week"
    else:
        start = today.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)
        label = start.strftime("%B %Y")

    return {
        "key": key,
        "label": label,
        "start": start,
        "end": end,
        "startDate": _iso(start),
        "endDate": _iso(end),
    }


def _previous_period_window(key: str, start: date, end: date) -> tuple[date, date]:
    if key == "today":
        previous = start - timedelta(days=1)
        return previous, previous
    if key == "week":
        previous_start = start - timedelta(days=7)
        return previous_start, previous_start + timedelta(days=6)

    previous_end = start - timedelta(days=1)
    return previous_end.replace(day=1), previous_end


def _period_comparison_label(key: str) -> str:
    if key == "today":
        return "vs yesterday"
    if key == "week":
        return "vs last week"
    return "vs last month"


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _appointment_session_status(appointment: Any) -> str:
    status_value = _status_value(appointment.status)
    if status_value in {"confirmed", "scheduled", "in_progress", "completed", "cancelled", "no_show"}:
        return "scheduled" if status_value == "confirmed" else status_value
    return "scheduled"


def _ensure_sessions_for_appointments(db: Session, user: Optional[User]) -> None:
    return None


def _invoice_ui_status(invoice: Invoice) -> str:
    raw = _status_value(invoice.status)
    balance = max(0, (invoice.total_amount or 0) - (invoice.paid_amount or 0))
    if raw == "issued" and invoice.due_date and invoice.due_date < date.today() and balance > 0:
        return "Overdue"
    if raw == "paid" or balance <= 0:
        return "Paid"
    if (invoice.paid_amount or 0) > 0:
        return "Partial"
    return "Pending"


def _analytics_payload(db: Session, user: Optional[User], period: Optional[str] = None) -> dict:
    today = date.today()
    first_of_month = today.replace(day=1)
    first_of_year = today.replace(month=1, day=1)
    period_info = _analytics_period_window(period)
    period_requested = period is not None
    period_key = period_info["key"]
    period_start = period_info["start"]
    period_end = period_info["end"]
    previous_period_start, previous_period_end = _previous_period_window(period_key, period_start, period_end)
    roles = _user_roles(db, user)
    current_therapist = _current_therapist(db, user) if _is_therapist_scoped_role(roles) else None
    patients = _base_query("patients", db, user).all()
    therapists = _base_query("therapists", db, user).all()
    invoices = _base_query("invoices", db, user).all()
    payments = _base_query("payments", db, user).all()
    patient_packages = _base_query("patient_packages", db, user).all()
    availability_rows = _base_query("therapist_availability", db, user).filter(
        TherapistAvailability.availability_date >= period_start,
        TherapistAvailability.availability_date <= period_end,
    ).all()
    revenue = sum(float(payment.payment_amount or 0) for payment in payments)
    receivables = sum(max(0, (invoice.total_amount or 0) - (invoice.paid_amount or 0)) for invoice in invoices)
    diagnosis_counts = Counter((patient.diagnosis or "Other").strip() or "Other" for patient in patients)
    palette = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#6b7280", "#8b5cf6"]
    total_diagnoses = sum(diagnosis_counts.values()) or 1
    diagnosis_data = [
        {"name": name, "value": round((count / total_diagnoses) * 100, 1), "count": count, "color": palette[i % len(palette)]}
        for i, (name, count) in enumerate(diagnosis_counts.most_common(6))
    ]
    return {
        "period": {k: v for k, v in period_info.items() if k not in {"start", "end"}},
        "stats": {
            "totalPatients": len(patients),
            "todayAppointments": 0,
            "activeSessions": 0,
            "monthRevenue": round(revenue, 2),
            "periodRevenue": round(revenue, 2),
            "unresolvedAlerts": 0,
            "completedSessionsToday": 0,
            "unbilledSessions": 0,
            "activeTherapists": sum(1 for therapist in therapists if therapist.is_active),
            "revenueGrowth": 0,
            "waitlistCount": 0,
        },
        "kpiCards": [
            {"title": "Revenue", "value": f"Rs {round(revenue):,}", "icon": "revenue", "change": "live payments", "changeType": "neutral"},
            {"title": "Receivables", "value": f"Rs {round(receivables):,}", "icon": "receivables", "change": f"{len(invoices)} invoices", "changeType": "neutral"},
            {"title": "Avg. Utilization", "value": "0%", "icon": "avg", "change": "slots table only", "changeType": "neutral"},
            {"title": "Active Packages", "value": str(sum(1 for pp in patient_packages if pp.status == "active")), "icon": "packages", "change": f"{len(patient_packages)} total", "changeType": "neutral"},
            {"title": "Leakage", "value": "0", "icon": "leakage", "change": "sessions table not present", "changeType": "neutral"},
        ],
        "revenueData": [],
        "sessionTrendData": [],
        "diagnosisData": diagnosis_data,
        "therapistUtilization": [],
        "therapistAvailability": [_availability_shape(row) for row in availability_rows],
        "billingRows": [_invoice_shape(invoice) for invoice in invoices],
        "unbilledSessions": [],
        "patientProgress": [],
        "waitlist": [],
        "therapistSessions": [],
    }

    patients = _base_query("patients", db, user).all()
    therapists = _base_query("therapists", db, user).all()
    sessions = _base_query("sessions", db, user).all()
    appointments = _base_query("appointments", db, user).all()
    invoices = _base_query("invoices", db, user).all()
    payments = _base_query("payments", db, user).all()
    alerts = _base_query("alerts", db, user).all()
    patient_packages = _base_query("patient_packages", db, user).all()
    waitlist_entries = []
    availability_rows = _base_query("therapist_availability", db, user).filter(
        TherapistAvailability.availability_date >= period_start,
        TherapistAvailability.availability_date <= period_end,
    ).all()

    completed_sessions = [s for s in sessions if _status_value(s.status) == "completed"]
    period_sessions = [s for s in sessions if period_start <= s.session_date <= period_end]
    completed_period_sessions = [s for s in period_sessions if _status_value(s.status) == "completed"]
    completed_today = [s for s in completed_sessions if s.session_date == today]
    unbilled_source_sessions = completed_period_sessions if period_requested else completed_sessions
    unbilled_sessions = [s for s in unbilled_source_sessions if s.billing_status not in {"billed", "paid"}]
    current_period_payments = [
        p for p in payments
        if _status_value(p.payment_status) in {"completed", "paid"}
        and (p.payment_date or p.created_at)
        and (
            (p.payment_date or p.created_at).date() >= first_of_month
            if not period_requested
            else period_start <= (p.payment_date or p.created_at).date() <= period_end
        )
    ]
    current_period_revenue = sum(float(p.payment_amount or 0) for p in current_period_payments)
    previous_period_revenue = sum(
        float(p.payment_amount or 0)
        for p in payments
        if _status_value(p.payment_status) in {"completed", "paid"}
        and (p.payment_date or p.created_at)
        and previous_period_start <= (p.payment_date or p.created_at).date() <= previous_period_end
    )
    revenue_growth = (
        round(((current_period_revenue - previous_period_revenue) / previous_period_revenue) * 100, 1)
        if previous_period_revenue else (100 if current_period_revenue else 0)
    )

    if period_requested:
        revenue_by_day = defaultdict(float)
        sessions_by_day = defaultdict(int)
        for payment in payments:
            paid_at = payment.payment_date or payment.created_at
            paid_date = paid_at.date() if paid_at else None
            if paid_date and period_start <= paid_date <= period_end and _status_value(payment.payment_status) in {"completed", "paid"}:
                revenue_by_day[paid_date] += float(payment.payment_amount or 0)
        for session in completed_period_sessions:
            sessions_by_day[session.session_date] += 1
        revenue_chart = []
        for day in _date_range(period_start, period_end):
            label = "Today" if period_key == "today" else day.strftime("%a" if period_key == "week" else "%b %d")
            revenue_chart.append({
                "label": label,
                "month": label,
                "revenue": round(revenue_by_day[day], 2),
                "sessions": sessions_by_day[day],
            })
    else:
        revenue_by_month = defaultdict(float)
        sessions_by_month = defaultdict(int)
        for payment in payments:
            paid_at = payment.payment_date or payment.created_at
            if paid_at and paid_at.date() >= first_of_year and _status_value(payment.payment_status) in {"completed", "paid"}:
                revenue_by_month[_month_key(paid_at)] += float(payment.payment_amount or 0)
        for session in completed_sessions:
            if session.session_date >= first_of_year:
                sessions_by_month[_month_key(session.session_date)] += 1
        revenue_chart = []
        for month in range(1, 13):
            d = today.replace(month=month, day=1)
            key = _month_key(d)
            label = _month_label(d)
            revenue_chart.append({
                "label": label,
                "month": label,
                "revenue": round(revenue_by_month[key], 2),
                "sessions": sessions_by_month[key],
            })

    trend_days = _date_range(period_start, period_end) if period_requested else [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    trend = []
    for day in trend_days:
        day_sessions = [s for s in sessions if s.session_date == day]
        label = "Today" if period_key == "today" else day.strftime("%a" if period_key == "week" else "%b %d")
        trend.append({
            "day": label,
            "completed": sum(1 for s in day_sessions if _status_value(s.status) == "completed"),
            "cancelled": sum(1 for s in day_sessions if _status_value(s.status) == "cancelled"),
            "noShow": sum(1 for s in day_sessions if _status_value(s.status) == "no_show"),
        })

    diagnosis_counts = Counter((p.diagnosis or "Other").strip() or "Other" for p in patients)
    palette = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#6b7280", "#8b5cf6"]
    total_diagnoses = sum(diagnosis_counts.values()) or 1
    diagnosis_data = [
        {"name": name, "value": round((count / total_diagnoses) * 100, 1), "count": count, "color": palette[i % len(palette)]}
        for i, (name, count) in enumerate(diagnosis_counts.most_common(6))
    ]

    utilization = []
    for therapist in therapists:
        therapist_sessions = [
            s for s in period_sessions
            if s.therapist_id == therapist.id
        ]
        completed = sum(1 for s in therapist_sessions if _status_value(s.status) == "completed")
        scheduled = sum(1 for s in therapist_sessions if _status_value(s.status) in {"scheduled", "in_progress"})
        booked_sessions = [s for s in therapist_sessions if _status_value(s.status) in {"scheduled", "in_progress", "completed"}]
        booked_minutes = sum(s.duration_minutes or 0 for s in booked_sessions)
        therapist_availability = [
            row for row in availability_rows
            if row.therapist_id == therapist.id and row.status == "available"
        ]
        available_minutes = sum(_availability_minutes(row) for row in therapist_availability)
        free_minutes = max(0, available_minutes - booked_minutes)
        total = len(therapist_sessions)
        utilization.append({
            "id": therapist.id,
            "name": therapist.name,
            "completedSessions": completed,
            "scheduledSessions": scheduled,
            "totalSessions": total,
            "bookedMinutes": booked_minutes,
            "availableMinutes": available_minutes,
            "freeMinutes": free_minutes,
            "availabilityDays": len(therapist_availability),
            "utilization": round((booked_minutes / available_minutes) * 100) if available_minutes else 0,
        })

    billing_rows = []
    for invoice in invoices:
        payment = next((p for p in payments if p.patient_id == invoice.patient_id and _status_value(p.payment_status) in {"completed", "paid"}), None)
        billing_rows.append({
            "id": invoice.id,
            "patientName": f"{invoice.patient.first_name} {invoice.patient.last_name}".strip() if invoice.patient else "Unknown",
            "patientCode": f"PT-{invoice.patient_id:04d}",
            "billingType": invoice.description or "Therapy Package",
            "amount": invoice.total_amount or 0,
            "paidAmount": invoice.paid_amount or 0,
            "balance": max(0, (invoice.total_amount or 0) - (invoice.paid_amount or 0)),
            "status": _invoice_ui_status(invoice),
            "paymentMode": payment.payment_mode_master.payment_mode_name if payment and payment.payment_mode_master else "Not paid",
            "dueDate": _iso(invoice.due_date),
        })

    package_price_by_patient = {
        pp.patient_id: (pp.package.price / pp.package.total_sessions)
        for pp in patient_packages
        if pp.package and pp.package.total_sessions
    }
    unbilled_rows = []
    for session in unbilled_sessions:
        amount = package_price_by_patient.get(session.patient_id, 1500)
        unbilled_rows.append({
            "id": session.id,
            "patientName": f"{session.patient.first_name} {session.patient.last_name}".strip() if session.patient else "Unknown",
            "patientCode": f"PT-{session.patient_id:04d}",
            "therapistName": session.therapist.name if session.therapist else "Unknown",
            "sessionDate": _iso(session.session_date),
            "amount": round(amount, 2),
        })

    patient_progress = []
    for patient in patients:
        patient_sessions = [s for s in sessions if s.patient_id == patient.id]
        active_package = next((pp for pp in patient_packages if pp.patient_id == patient.id and pp.status == "active"), None)
        package_stats = _package_session_stats(active_package) if active_package else None
        package_sessions = package_stats["sessions"] if package_stats else patient_sessions
        completed = package_stats["completed"] if package_stats else sum(1 for s in patient_sessions if _status_value(s.status) == "completed")
        scheduled = package_stats["scheduled"] if package_stats else sum(1 for s in patient_sessions if _status_value(s.status) in {"scheduled", "in_progress"})
        remaining = package_stats["remaining"] if package_stats else max(0, len(patient_sessions) - completed)
        total_slots = package_stats["total"] if package_stats else len(patient_sessions)
        current_session = next(
            (
                s for s in sorted(package_sessions, key=lambda row: row.session_date)
                if _status_value(s.status) in {"in_progress", "scheduled"} and s.session_date >= today
            ),
            None,
        )
        patient_progress.append({
            "patientId": patient.id,
            "patientName": f"{patient.first_name} {patient.last_name}".strip(),
            "patientCode": f"PT-{patient.id:04d}",
            "totalSlots": total_slots,
            "completedSlots": completed,
            "scheduledSlots": scheduled,
            "remainingSlots": remaining,
            "progressPercent": round((completed / total_slots) * 100) if total_slots else 0,
            "currentTherapist": current_session.therapist.name if current_session and current_session.therapist else None,
            "currentStatus": _status_value(current_session.status) if current_session else None,
            "nextSessionDate": _iso(current_session.session_date) if current_session else None,
        })

    comparison_label = _period_comparison_label(period_key)
    leakage_change = (
        f"Rs {round(sum(r['amount'] for r in unbilled_rows)):,} unbilled in {period_info['label']}"
        if period_requested
        else f"Rs {round(sum(r['amount'] for r in unbilled_rows)):,} unbilled"
    )
    kpis = [
        {"title": "Revenue", "value": f"Rs {round(current_period_revenue):,}", "icon": "revenue", "change": f"{revenue_growth}% {comparison_label}", "changeType": "up" if revenue_growth >= 0 else "down"},
        {"title": "Receivables", "value": f"Rs {round(sum(r['balance'] for r in billing_rows)):,}", "icon": "receivables", "change": f"{sum(1 for r in billing_rows if r['balance'] > 0)} invoices pending", "changeType": "down"},
        {"title": "Avg. Utilization", "value": f"{round(sum(t['utilization'] for t in utilization) / len(utilization)) if utilization else 0}%", "icon": "avg", "change": "booked vs available time", "changeType": "neutral"},
        {"title": "Active Packages", "value": str(sum(1 for pp in patient_packages if pp.status == "active")), "icon": "packages", "change": f"{len(patient_packages)} total", "changeType": "neutral"},
        {"title": "Leakage", "value": str(len(unbilled_sessions)), "icon": "leakage", "change": leakage_change, "changeType": "down"},
    ]

    return {
        "period": {k: v for k, v in period_info.items() if k not in {"start", "end"}},
        "stats": {
            "totalPatients": len(patients),
            "todayAppointments": sum(1 for a in appointments if a.start_time.date() == today),
            "activeSessions": sum(1 for s in sessions if _status_value(s.status) == "in_progress"),
            "monthRevenue": round(current_period_revenue, 2),
            "periodRevenue": round(current_period_revenue, 2),
            "unresolvedAlerts": sum(1 for a in alerts if a.is_active),
            "completedSessionsToday": len(completed_today),
            "unbilledSessions": len(unbilled_sessions),
            "activeTherapists": sum(1 for t in therapists if t.is_available),
            "revenueGrowth": revenue_growth,
            "waitlistCount": len([w for w in waitlist_entries if w.status == "waiting"]),
        },
        "kpiCards": kpis,
        "revenueData": revenue_chart,
        "sessionTrendData": trend,
        "diagnosisData": diagnosis_data,
        "therapistUtilization": utilization,
        "therapistAvailability": [_availability_shape(row) for row in availability_rows],
        "billingRows": billing_rows,
        "unbilledSessions": unbilled_rows,
        "patientProgress": patient_progress,
        "waitlist": [_waitlist_shape(w) for w in waitlist_entries],
        "therapistSessions": [_session_shape(s) for s in sessions] if current_therapist else [],
    }


def _session_report_month_key(value: date) -> str:
    return value.strftime("%Y-%m")


def _session_report_month_label(value: date) -> str:
    return value.strftime("%B %Y")


def _slot_status_code(slot_booking: PatientSlotBooking) -> str:
    status = getattr(slot_booking, "patient_slot_booking_status_master", None)
    return str(getattr(status, "code", "") or "").upper()


def _slot_is_conducted(slot_booking: PatientSlotBooking) -> bool:
    status = _slot_status_code(slot_booking)
    if status in {"CANCELLED", "UNPAID_CANCELLED", "NO_SHOW"}:
        return False
    return True


def _slot_is_paid(slot_booking: PatientSlotBooking, amount: float, paid: float, status: str) -> bool:
    return bool(
        getattr(slot_booking, "fully_paid", False)
        or status in {"PAID", "COMPLETED", "PACKAGE_COVERED"}
        or (amount > 0 and paid >= amount)
    )


def _is_general_report_session(slot_booking: PatientSlotBooking) -> bool:
    program_name = (
        slot_booking.program.program_name
        if getattr(slot_booking, "program", None)
        else ""
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", program_name.lower()).strip()
    if not normalized:
        return True
    excluded_tokens = (
        "crt",
        "structured",
        "vocational",
        "social",
        "parent training",
        "prewriting",
        "prewritting",
        "peg",
        "school",
    )
    return not any(token in normalized for token in excluded_tokens)


def _therapist_session_report_payload(
    db: Session,
    user: Optional[User],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    therapist_id: Optional[int] = None,
    region_id: Optional[int] = None,
) -> dict:
    today = date.today()
    period_start = _parse_date(start_date) or today.replace(day=1)
    next_month = (period_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    period_end = _parse_date(end_date) or (next_month - timedelta(days=1))
    if period_end < period_start:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")

    roles = _user_roles(db, user)
    current_therapist = _current_therapist(db, user) if _is_therapist_scoped_role(roles) else None
    if current_therapist:
        therapist_id = current_therapist.id

    region_ids = _user_region_ids(db, user)
    if region_id and region_ids and region_id not in region_ids:
        raise HTTPException(status_code=403, detail="Region is outside your access")

    query = (
        db.query(PatientSlotBooking)
        .options(
            joinedload(PatientSlotBooking.patient),
            joinedload(PatientSlotBooking.patient_slot_booking_status_master),
            joinedload(PatientSlotBooking.patient_package).joinedload(PatientPackage.package),
            joinedload(PatientSlotBooking.crt_program_booking),
            joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.therapist),
            joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.therapy),
            joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.slot),
        )
        .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
        .join(Therapist, Therapist.id == TherapistSlotMapping.therapist_id)
        .filter(
            TherapistSlotMapping.slot_date >= period_start,
            TherapistSlotMapping.slot_date <= period_end,
        )
    )

    if therapist_id:
        query = query.filter(TherapistSlotMapping.therapist_id == therapist_id)
    if region_id:
        query = query.filter(Therapist.region_id == region_id)
    elif region_ids:
        query = query.filter(Therapist.region_id.in_(region_ids))

    rows = query.order_by(Therapist.name, TherapistSlotMapping.slot_date, PatientSlotBooking.id).all()
    therapists: dict[int, dict] = {}
    grand = {
        "totalSessions": 0,
        "paidSessions": 0,
        "sessionAmount": 0.0,
        "paidAmount": 0.0,
        "therapistAmount": 0.0,
        "dueAmount": 0.0,
    }

    for row in rows:
        mapping = row.therapist_slot_mapping
        if not mapping or not _slot_is_conducted(row):
            continue
        if not _is_general_report_session(row):
            continue

        therapist = mapping.therapist
        therapist_key = mapping.therapist_id
        session_date = mapping.slot_date
        status = str(row.payment_status or "").upper()
        amount = float(
            row.crt_program_booking.total_amount
            if row.crt_program_booking_id and row.crt_program_booking
            else row.amount
            or 0
        )
        raw_paid = float(
            row.crt_program_booking.paid_amount
            if row.crt_program_booking_id and row.crt_program_booking
            else row.paid_amount
            or 0
        )
        package_covered = float(
            row.crt_program_booking.package_covered_amount
            if row.crt_program_booking_id and row.crt_program_booking
            else getattr(row, "package_covered_amount", 0)
            or 0
        )
        is_package_involved = bool(row.is_package_session or row.patient_package_id or package_covered > 0)
        paid = raw_paid + package_covered if is_package_involved else raw_paid
        due = 0.0 if _slot_is_paid(row, amount, paid, status) else max(0.0, amount - paid)
        is_paid = _slot_is_paid(row, amount, paid, status)
        therapist_amount = paid if is_package_involved else (paid if is_paid else amount)
        month_key = _session_report_month_key(session_date)

        therapist_row = therapists.setdefault(
            therapist_key,
            {
                "therapistId": therapist_key,
                "therapistName": therapist.name if therapist else f"Therapist {therapist_key}",
                "regionId": therapist.region_id if therapist else None,
                "totalSessions": 0,
                "paidSessions": 0,
                "sessionAmount": 0.0,
                "paidAmount": 0.0,
                "therapistAmount": 0.0,
                "dueAmount": 0.0,
                "monthlySummary": {},
                "sessions": [],
            },
        )
        monthly = therapist_row["monthlySummary"].setdefault(
            month_key,
            {
                "month": month_key,
                "label": _session_report_month_label(session_date),
                "totalSessions": 0,
                "paidSessions": 0,
                "sessionAmount": 0.0,
                "paidAmount": 0.0,
                "therapistAmount": 0.0,
                "dueAmount": 0.0,
            },
        )

        patient_name = (
            f"{row.patient.first_name} {row.patient.last_name}".strip()
            if row.patient
            else "Unknown"
        )
        slot = mapping.slot
        group_booking = row.group_program_booking
        crt_booking = row.crt_program_booking
        start_time = (
            getattr(group_booking, "start_time", None)
            or getattr(slot, "start_time", None)
            or getattr(getattr(crt_booking, "slot", None), "start_time", None)
        )
        end_time = (
            getattr(group_booking, "end_time", None)
            or getattr(slot, "end_time", None)
            or getattr(getattr(crt_booking, "slot", None), "end_time", None)
            or _time_with_duration(start_time, row.duration_minutes)
        )
        time_slot = (
            f"{_iso(start_time)} - {_iso(end_time)}"
            if start_time and end_time
            else None
        )
        detail = {
            "sessionId": row.id,
            "date": _iso(session_date),
            "month": month_key,
            "startTime": _iso(start_time),
            "endTime": _iso(end_time),
            "timeSlot": time_slot,
            "patientId": row.patient_id,
            "patientName": patient_name,
            "therapyName": mapping.therapy.name if mapping.therapy else None,
            "status": _slot_status_code(row) or "BOOKED",
            "paymentStatus": status or ("PAID" if is_paid else "UNPAID"),
            "sessionAmount": round(amount, 2),
            "paidAmount": round(paid, 2),
            "cashPaidAmount": round(raw_paid, 2),
            "packageCoveredAmount": round(package_covered, 2),
            "therapistAmount": round(therapist_amount, 2),
            "dueAmount": round(due, 2),
            "fullyPaid": is_paid,
        }

        for bucket in (therapist_row, monthly, grand):
            bucket["totalSessions"] += 1
            bucket["paidSessions"] += 1 if is_paid else 0
            bucket["sessionAmount"] += amount
            bucket["paidAmount"] += paid
            bucket["therapistAmount"] += therapist_amount
            bucket["dueAmount"] += due
        therapist_row["sessions"].append(detail)

    therapist_list = []
    for therapist_row in therapists.values():
        therapist_row["monthlySummary"] = sorted(therapist_row["monthlySummary"].values(), key=lambda item: item["month"])
        therapist_row["sessions"] = sorted(therapist_row["sessions"], key=lambda item: (item["date"], item["sessionId"]))
        for key in ("sessionAmount", "paidAmount", "therapistAmount", "dueAmount"):
            therapist_row[key] = round(therapist_row[key], 2)
            for monthly in therapist_row["monthlySummary"]:
                monthly[key] = round(monthly[key], 2)
        therapist_list.append(therapist_row)

    therapist_list.sort(key=lambda item: item["therapistName"].lower())
    for key in ("sessionAmount", "paidAmount", "therapistAmount", "dueAmount"):
        grand[key] = round(grand[key], 2)

    return {
        "period": {
            "startDate": _iso(period_start),
            "endDate": _iso(period_end),
            "label": f"{period_start.strftime('%d %b %Y')} - {period_end.strftime('%d %b %Y')}",
        },
        "regionId": region_id,
        "totals": grand,
        "therapists": therapist_list,
    }


@router.get("/analytics")
async def analytics(request: Request, period: Optional[str] = None, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    return {"data": _analytics_payload(db, user, period)}


@router.get("/reports/therapist-sessions")
async def therapist_session_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    therapist_id: Optional[int] = None,
    region_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    return {"data": _therapist_session_report_payload(db, user, start_date, end_date, therapist_id, region_id)}


@router.get("/patients/{patient_id}")
async def get_patient_detail(
    patient_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _current_user(request, db)
    patient = _base_query("patients", db, user).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"data": _patient_shape(patient)}


@router.get("/patients/{patient_id}/transactions")
async def get_patient_transactions(
    patient_id: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    user = _current_user(request, db)
    patient = _base_query("patients", db, user).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    packages = (
        db.query(PatientPackage)
        .options(joinedload(PatientPackage.package))
        .filter(PatientPackage.patient_id == patient_id, PatientPackage.deleted_at.is_(None))
        .order_by(PatientPackage.created_at.desc(), PatientPackage.id.desc())
        .all()
    )
    active_package = next((pkg for pkg in packages if str(pkg.status or "").lower() == "active"), packages[0] if packages else None)

    slots = (
        db.query(PatientSlotBooking)
        .options(
            joinedload(PatientSlotBooking.patient_package).joinedload(PatientPackage.package),
            joinedload(PatientSlotBooking.crt_program_booking).joinedload(CrtProgramBooking.patient_package).joinedload(PatientPackage.package),
            joinedload(PatientSlotBooking.patient_session_plan_item),
            joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.slot),
            joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.therapy),
        )
        .outerjoin(PatientSessionPlanItem, PatientSessionPlanItem.id == PatientSlotBooking.patient_session_plan_item_id)
        .outerjoin(PatientSessionPlan, PatientSessionPlan.id == PatientSessionPlanItem.patient_session_plan_id)
        .filter(or_(PatientSlotBooking.patient_id == patient_id, PatientSessionPlan.patient_id == patient_id))
        .order_by(PatientSlotBooking.created_at.desc(), PatientSlotBooking.id.desc())
        .all()
    )
    for slot_booking in slots:
        linked_package = slot_booking.patient_package
        if (
            slot_booking.is_package_session
            and linked_package
            and slot_booking.created_at
            and linked_package.created_at
            and slot_booking.created_at < linked_package.created_at
        ):
            slot_booking.patient_package_id = None
            slot_booking.is_package_session = False
            slot_booking.patient_package = None
            _recalculate_slot_payment(slot_booking)
            continue
        if slot_booking.is_package_session and slot_booking.patient_package:
            continue
        _recalculate_slot_payment(slot_booking)
    crt_parents = (
        db.query(CrtProgramBooking)
        .options(
            joinedload(CrtProgramBooking.program),
            joinedload(CrtProgramBooking.slot),
            joinedload(CrtProgramBooking.patient_package).joinedload(PatientPackage.package),
        )
        .filter(CrtProgramBooking.patient_id == patient_id)
        .order_by(CrtProgramBooking.created_at.desc(), CrtProgramBooking.id.desc())
        .all()
    )
    slot_payments: dict[int, Payment] = {}
    payments_by_slot: dict[int, list[Payment]] = defaultdict(list)
    payments_by_crt_parent: dict[int, list[Payment]] = defaultdict(list)
    payments_by_package: dict[int, list[Payment]] = defaultdict(list)
    assessment_payments: list[Payment] = []
    payment_rows = (
        db.query(Payment)
        .options(joinedload(Payment.payment_mode_master))
        .filter(
            Payment.patient_id == patient_id,
            or_(
                Payment.crt_program_booking_id.isnot(None),
                Payment.patient_slot_booking_id.isnot(None),
                Payment.remark.like("[slot:%"),
                Payment.remark.like("[package:%"),
                Payment.remark.like("[assessment:%"),
                Payment.remark.like("[assessment-payment:%"),
            ),
        )
        .order_by(Payment.payment_date.desc(), Payment.created_at.desc(), Payment.id.desc())
        .all()
    )
    assessment_billings = (
        db.query(PatientAssessmentBilling)
        .options(
            joinedload(PatientAssessmentBilling.assessment),
            joinedload(PatientAssessmentBilling.source_payment),
        )
        .filter(PatientAssessmentBilling.patient_id == patient_id)
        .order_by(PatientAssessmentBilling.completed_at.desc(), PatientAssessmentBilling.created_at.desc(), PatientAssessmentBilling.id.desc())
        .all()
    )
    slots_by_crt_parent: dict[int, list[PatientSlotBooking]] = defaultdict(list)
    slots_by_group_child: dict[tuple[int, int], list[PatientSlotBooking]] = defaultdict(list)
    for slot_booking in slots:
        if slot_booking.crt_program_booking_id:
            slots_by_crt_parent[int(slot_booking.crt_program_booking_id)].append(slot_booking)
        if getattr(slot_booking, "group_program_booking_id", None) and slot_booking.patient_id:
            slots_by_group_child[(int(slot_booking.group_program_booking_id), int(slot_booking.patient_id))].append(slot_booking)
    crt_parents_by_id = {int(crt_parent.id): crt_parent for crt_parent in crt_parents}
    group_payable_slot_by_slot_id: dict[int, PatientSlotBooking] = {}
    for group_siblings in slots_by_group_child.values():
        payable_slot = max(
            group_siblings,
            key=lambda sibling: (
                bool(sibling.is_package_session),
                float(sibling.due_amount or 0),
                float(sibling.amount or _slot_booking_amount(sibling) or 0),
                int(sibling.id or 0),
            ),
        )
        total_paid = sum(float(sibling.paid_amount or 0) for sibling in group_siblings)
        total_package_covered = sum(float(getattr(sibling, "package_covered_amount", 0) or 0) for sibling in group_siblings)
        amount = max(float(sibling.amount or _slot_booking_amount(sibling) or 0) for sibling in group_siblings)
        fully_paid = any(bool(getattr(sibling, "fully_paid", False)) for sibling in group_siblings) or (amount > 0 and total_paid + total_package_covered >= amount)
        payable_slot.amount = amount
        payable_slot.paid_amount = total_paid
        payable_slot.package_covered_amount = total_package_covered
        payable_slot.due_amount = 0 if fully_paid else max(0, amount - total_paid - total_package_covered)
        payable_slot.fully_paid = fully_paid
        payable_slot.payment_status = "PAID" if fully_paid else ("PARTIAL" if total_paid > 0 or total_package_covered > 0 else "UNPAID")
        for sibling in group_siblings:
            group_payable_slot_by_slot_id[int(sibling.id)] = payable_slot
            if sibling.id == payable_slot.id:
                continue
            sibling.amount = 0
            sibling.paid_amount = 0
            sibling.package_covered_amount = 0
            sibling.due_amount = 0
            sibling.fully_paid = fully_paid
            sibling.payment_status = payable_slot.payment_status
    if group_payable_slot_by_slot_id:
        db.flush()
    slots_by_id = {slot_booking.id: slot_booking for slot_booking in slots}
    managed_package_credit_keys: set[tuple[str, int]] = set()
    for payment in payment_rows:
        remark = payment.remark or ""
        if "Payment done for Package credit used" not in remark and not str(getattr(payment, "payment_note", "") or "").startswith("Allocated from package payment"):
            continue
        if getattr(payment, "crt_program_booking_id", None):
            managed_package_credit_keys.add(("crt", int(payment.crt_program_booking_id)))
        marker = remark.split("]", 1)[0] if "]" in remark else ""
        if marker.startswith("[slot:"):
            try:
                managed_package_credit_keys.add(("slot", int(marker.replace("[slot:", ""))))
            except ValueError:
                pass
    for payment in payment_rows:
        remark = payment.remark or ""
        is_legacy_package_credit = "Package credit used" in remark and "Payment done for Package credit used" not in remark
        if is_legacy_package_credit:
            if getattr(payment, "crt_program_booking_id", None) and ("crt", int(payment.crt_program_booking_id)) in managed_package_credit_keys:
                continue
            marker = remark.split("]", 1)[0] if "]" in remark else ""
            if marker.startswith("[slot:"):
                try:
                    if ("slot", int(marker.replace("[slot:", ""))) in managed_package_credit_keys:
                        continue
                except ValueError:
                    pass
        if getattr(payment, "crt_program_booking_id", None):
            payments_by_crt_parent[int(payment.crt_program_booking_id)].append(payment)
            crt_slots = slots_by_crt_parent.get(int(payment.crt_program_booking_id), [])
            if crt_slots:
                marker = (payment.remark or "").split("]", 1)[0] if "]" in (payment.remark or "") else ""
                marked_slot_id = None
                if marker.startswith("[slot:"):
                    try:
                        marked_slot_id = int(marker.replace("[slot:", ""))
                    except ValueError:
                        marked_slot_id = None
                payable_slot = (
                    next((row for row in crt_slots if row.id == marked_slot_id), None)
                    or max(crt_slots, key=lambda row: (float(row.amount or _slot_booking_amount(row) or 0), -int(row.id or 0)))
                )
                slot_payments.setdefault(payable_slot.id, payment)
                payments_by_slot[payable_slot.id].append(payment)
                continue
        if getattr(payment, "patient_slot_booking_id", None):
            slot_id = int(payment.patient_slot_booking_id)
            slot_id = int(group_payable_slot_by_slot_id.get(slot_id, slots_by_id.get(slot_id)).id) if (slot_id in group_payable_slot_by_slot_id or slots_by_id.get(slot_id)) else slot_id
            slot_payments.setdefault(slot_id, payment)
            payments_by_slot[slot_id].append(payment)
            continue
        remark = payment.remark or ""
        if "]" not in remark:
            continue
        marker = remark.split("]", 1)[0]
        if marker.startswith("[slot:"):
            try:
                slot_id = int(marker.replace("[slot:", ""))
            except ValueError:
                continue
            slot_id = int(group_payable_slot_by_slot_id.get(slot_id, slots_by_id.get(slot_id)).id) if (slot_id in group_payable_slot_by_slot_id or slots_by_id.get(slot_id)) else slot_id
            slot_payments.setdefault(slot_id, payment)
            payments_by_slot[slot_id].append(payment)
        elif marker.startswith("[package:"):
            try:
                package_id = int(marker.replace("[package:", ""))
            except ValueError:
                continue
            payments_by_package[package_id].append(payment)
        elif marker.startswith("[assessment:") or marker.startswith("[assessment-payment:"):
            assessment_payments.append(payment)

    normalized_package_due_by_id = _normalize_patient_package_due_values(packages)
    normalized_payment_due_by_id = {
        int(payment.id): float(payment.due_amount or 0)
        for payment in payment_rows
        if payment.id
    }

    package_payment_totals = {
        package_id: sum(float(payment.payment_amount or 0) for payment in payments)
        for package_id, payments in payments_by_package.items()
    }
    package_slot_payment_totals: dict[int, float] = defaultdict(float)
    for slot_id, payments in payments_by_slot.items():
        slot_booking = slots_by_id.get(slot_id)
        if not slot_booking or not slot_booking.patient_package_id:
            continue
        package_slot_payment_totals[int(slot_booking.patient_package_id)] += sum(
            float(payment.payment_amount or 0) for payment in payments
        )
    package_initial_paid = {
        pkg.id: max(
            0,
            _package_effective_paid_amount(pkg)
            - package_payment_totals.get(pkg.id, 0)
            - package_slot_payment_totals.get(pkg.id, 0),
        )
        for pkg in packages
    }

    transaction_rows = []
    for patient_package in packages:
        if payments_by_package.get(patient_package.id):
            continue
        transaction_rows.append(_package_purchase_transaction_shape(
            patient_package,
            package_initial_paid.get(patient_package.id, 0),
        ))

    packages_by_id = {pkg.id: pkg for pkg in packages}
    package_running_due = {
        pkg.id: -max(0, package_initial_paid.get(pkg.id, 0) + package_payment_totals.get(pkg.id, 0))
        for pkg in packages
    }
    seen_crt_package_usage: set[tuple[int, int | None, str]] = set()
    emitted_crt_direct_usage: set[tuple[int, int | None, str]] = set()
    emitted_crt_parent_ids: set[int] = set()

    def slot_is_transaction_relevant(slot_booking: PatientSlotBooking) -> bool:
        status_value = str(slot_booking.status or "").upper()
        return (
            status_value in {"COMPLETED", "PAID_CANCELLED"}
            or bool(payments_by_slot.get(slot_booking.id))
        )

    for slot_booking in sorted(slots, key=lambda row: (row.created_at or _now(), row.id or 0)):
        if slot_booking.crt_program_booking_id:
            crt_parent_id = int(slot_booking.crt_program_booking_id)
            if crt_parent_id in emitted_crt_parent_ids:
                continue
            crt_siblings = slots_by_crt_parent.get(crt_parent_id, [slot_booking])
            relevant_crt_siblings = [sibling for sibling in crt_siblings if slot_is_transaction_relevant(sibling)]
            if not relevant_crt_siblings:
                continue
            emitted_crt_parent_ids.add(crt_parent_id)
            slot_booking = sorted(
                relevant_crt_siblings,
                key=lambda sibling: (
                    not bool(sibling.is_package_session),
                    -(float(sibling.amount or _slot_booking_amount(sibling) or 0)),
                    int(sibling.id or 0),
                ),
            )[0]
        elif not slot_is_transaction_relevant(slot_booking):
            continue
        program_name = (slot_booking.program.program_name if slot_booking.program else "") or ""
        is_crt_slot = "crt" in program_name.lower() or "structured" in program_name.lower()
        mapping = slot_booking.therapist_slot_mapping
        slot = mapping.slot if mapping else None
        slot_date_key = "|".join([
            _iso(mapping.slot_date if mapping else None) or "",
            str(getattr(slot, "start_time", "") or ""),
            str(getattr(slot, "end_time", "") or ""),
            str(slot_booking.id or ""),
        ])
        row = _transaction_slot_shape(slot_booking, slot_payments.get(slot_booking.id))
        row_amount = float(row["amount"] or 0)
        row_paid = float(row["paid_amount"] or 0)
        row_status = str(row.get("payment_status") or "").lower()
        row_is_fully_paid = bool(getattr(slot_booking, "fully_paid", False)) or row_status in {"package_covered"} or (row_amount > 0 and row_paid >= row_amount)
        if slot_booking.is_package_session and slot_booking.patient_package_id:
            row_paid = 0
            row["paid_amount"] = 0
            row["fully_paid"] = False
            row_is_fully_paid = False
            package_id = int(slot_booking.patient_package_id)
            crt_usage_key = (
                package_id,
                int(slot_booking.crt_program_booking_id) if slot_booking.crt_program_booking_id else None,
                slot_date_key,
            )
            if is_crt_slot and crt_usage_key in seen_crt_package_usage:
                continue
            if is_crt_slot:
                seen_crt_package_usage.add(crt_usage_key)
            patient_package = packages_by_id.get(package_id)
            package_running = float(package_running_due.get(package_id, 0) or 0)
            row_due = package_running if package_running > 0 else (float(getattr(patient_package, "due_amount", 0) or 0) if patient_package else 0)
            package_running_due[package_id] = row_due
            row["due_amount"] = row_due
        elif not slot_booking.is_package_session:
            if is_crt_slot:
                crt_payment_count = sum(
                    len(payments_by_slot.get(sibling.id, []))
                    for sibling in slots_by_crt_parent.get(int(slot_booking.crt_program_booking_id or 0), [slot_booking])
                )
                if crt_payment_count > 0:
                    continue
                crt_usage_key = (
                    slot_booking.patient_id or patient_id,
                    int(slot_booking.crt_program_booking_id) if slot_booking.crt_program_booking_id else None,
                    slot_date_key,
                )
                if crt_usage_key in emitted_crt_direct_usage:
                    continue
                crt_siblings = [
                    sibling for sibling in slots
                    if (sibling.patient_id or patient_id) == (slot_booking.patient_id or patient_id)
                    and sibling.program_id == slot_booking.program_id
                    and sibling.crt_program_booking_id == slot_booking.crt_program_booking_id
                    and "|".join([
                        _iso(sibling.therapist_slot_mapping.slot_date if sibling.therapist_slot_mapping else None) or "",
                        str(getattr(sibling.therapist_slot_mapping.slot if sibling.therapist_slot_mapping else None, "start_time", "") or ""),
                        str(getattr(sibling.therapist_slot_mapping.slot if sibling.therapist_slot_mapping else None, "end_time", "") or ""),
                        str(sibling.id or ""),
                    ]) == slot_date_key
                    and not sibling.is_package_session
                    and (
                        "crt" in ((sibling.program.program_name if sibling.program else "") or "").lower()
                        or "structured" in ((sibling.program.program_name if sibling.program else "") or "").lower()
                    )
                ]
                total_paid = sum(float(sibling.paid_amount or 0) for sibling in crt_siblings)
                payable_sibling = max(
                    crt_siblings,
                    key=lambda sibling: (
                        float(sibling.amount or _slot_booking_amount(sibling) or 0),
                        int(sibling.id or 0),
                    ),
                    default=slot_booking,
                )
                if payable_sibling.id != slot_booking.id:
                    row = _transaction_slot_shape(payable_sibling, slot_payments.get(payable_sibling.id))
                    row_amount = float(row["amount"] or 0)
                    row_paid = total_paid
                    row["paid_amount"] = row_paid
                    row_is_fully_paid = bool(getattr(payable_sibling, "fully_paid", False)) or (row_amount > 0 and row_paid >= row_amount)
                emitted_crt_direct_usage.add(crt_usage_key)
            row["due_amount"] = 0 if row_is_fully_paid else max(0, row_amount - row_paid)
            if not is_crt_slot and payments_by_slot.get(slot_booking.id):
                continue
        # Transaction history is payment-ledger only. Session/package context rows
        # remain available through payable_slots but are not shown as transactions.

    for crt_parent in sorted(crt_parents, key=lambda row: (row.created_at or _now(), row.id or 0), reverse=True):
        crt_parent_id = int(crt_parent.id)
        if crt_parent_id in emitted_crt_parent_ids:
            continue
        _sync_crt_parent_payment_from_ledger(db, crt_parent)
        has_payment = bool(payments_by_crt_parent.get(crt_parent_id))
        if (
            not has_payment
            and bool(crt_parent.fully_paid)
            and float(crt_parent.due_amount or 0) <= 0
        ):
            continue
        if (
            not has_payment
            and float(crt_parent.due_amount or 0) <= 0
            and str(crt_parent.payment_status or "").upper() in {"PAID", "PACKAGE_COVERED"}
        ):
            continue
        transaction_rows.append(_crt_parent_transaction_shape(
            crt_parent,
            payments_by_crt_parent.get(crt_parent_id, [None])[0],
        ))

    package_running_due = {
        pkg.id: -max(0, package_initial_paid.get(pkg.id, 0) + package_payment_totals.get(pkg.id, 0))
        for pkg in packages
    }

    def package_credit_payment_due(slot_booking: PatientSlotBooking, payment: Payment) -> Optional[float]:
        remark = str(payment.remark or "")
        note = str(getattr(payment, "payment_note", "") or "")
        crt_parent = slot_booking.crt_program_booking
        is_package_session = bool(getattr(slot_booking, "is_package_session", False)) or bool(getattr(crt_parent, "is_package_session", False))
        patient_package_id = slot_booking.patient_package_id or getattr(crt_parent, "patient_package_id", None)
        is_package_credit_use = (
            "Package credit used" in remark
            or "Session completed" in remark
            or note.startswith("Allocated from package payment")
        )
        if (
            not is_package_session
            or not patient_package_id
            or not is_package_credit_use
        ):
            return None
        package_id = int(patient_package_id)
        row_amount = float(getattr(payment, "amount", 0) or 0) or float(slot_booking.amount or _slot_booking_amount(slot_booking) or 0)
        payment_amount = float(payment.payment_amount or 0)
        payment_created_at = payment.created_at or payment.payment_date or _now()
        package_credit_before = sum(
            float(package_payment.payment_amount or 0)
            for package_payment in payments_by_package.get(package_id, [])
            if (package_payment.created_at or package_payment.payment_date or _now()) <= payment_created_at
        )
        package_usage_before = 0.0
        seen_usage_payment_ids: set[int] = set()
        for sibling_slot in slots:
            sibling_parent = sibling_slot.crt_program_booking
            sibling_package_id = sibling_slot.patient_package_id or getattr(sibling_parent, "patient_package_id", None)
            if sibling_package_id != package_id:
                continue
            sibling_payments = payments_by_crt_parent.get(int(sibling_slot.crt_program_booking_id), []) if sibling_slot.crt_program_booking_id else payments_by_slot.get(sibling_slot.id, [])
            for sibling_payment in sibling_payments:
                sibling_payment_id = int(sibling_payment.id or 0)
                if sibling_payment_id in seen_usage_payment_ids:
                    continue
                sibling_created_at = sibling_payment.created_at or sibling_payment.payment_date or _now()
                if sibling_payment_id == int(payment.id or 0) or sibling_created_at > payment_created_at:
                    continue
                sibling_remark = str(sibling_payment.remark or "")
                sibling_note = str(getattr(sibling_payment, "payment_note", "") or "")
                if (
                    "Package credit used" in sibling_remark
                    or "Session completed" in sibling_remark
                    or sibling_note.startswith("Allocated from package payment")
                ):
                    seen_usage_payment_ids.add(sibling_payment_id)
                    usage_amount = float(getattr(sibling_payment, "amount", 0) or 0) or float(sibling_slot.amount or _slot_booking_amount(sibling_slot) or 0)
                    if bool(getattr(sibling_payment, "fully_paid", False)) and "Marked as fully paid" in sibling_note:
                        usage_amount = max(0, usage_amount - _payment_waived_amount(sibling_payment))
                    package_usage_before += usage_amount
        current_due = package_usage_before - package_credit_before
        credit_available = max(0, -current_due)
        next_due = current_due + row_amount - payment_amount
        package_running_due[package_id] = next_due
        if next_due < 0:
            return next_due
        return max(0, next_due)

    def package_session_running_due(slot_booking: PatientSlotBooking, payment: Payment) -> Optional[float]:
        crt_parent = slot_booking.crt_program_booking
        is_package_session = bool(getattr(slot_booking, "is_package_session", False)) or bool(getattr(crt_parent, "is_package_session", False))
        package_id = slot_booking.patient_package_id or getattr(crt_parent, "patient_package_id", None)
        if not is_package_session or not package_id:
            return None
        payment_created_at = payment.created_at or payment.payment_date or _now()
        package_payments = [
            row for row in payments_by_package.get(int(package_id), [])
            if (row.created_at or row.payment_date or _now()) <= payment_created_at
        ]
        if not package_payments:
            return float(getattr(payment, "due_amount", 0) or 0)
        latest_package_payment = sorted(
            package_payments,
            key=lambda row: (row.created_at or row.payment_date or _now(), row.id or 0),
            reverse=True,
        )[0]
        running_due = float(getattr(latest_package_payment, "due_amount", 0) or 0)
        latest_package_payment_at = latest_package_payment.created_at or latest_package_payment.payment_date or _now()
        seen_payment_ids: set[int] = set()
        charged_usage_keys: set[tuple[str, int]] = set()
        usage_payments: list[tuple[datetime, int, PatientSlotBooking, Payment]] = []
        for sibling_slot in slots:
            sibling_parent = sibling_slot.crt_program_booking
            sibling_package_id = sibling_slot.patient_package_id or getattr(sibling_parent, "patient_package_id", None)
            if int(sibling_package_id or 0) != int(package_id):
                continue
            sibling_payments = payments_by_crt_parent.get(int(sibling_slot.crt_program_booking_id), []) if sibling_slot.crt_program_booking_id else payments_by_slot.get(sibling_slot.id, [])
            for sibling_payment in sibling_payments:
                sibling_payment_id = int(sibling_payment.id or 0)
                if sibling_payment_id in seen_payment_ids:
                    continue
                seen_payment_ids.add(sibling_payment_id)
                sibling_created_at = sibling_payment.created_at or sibling_payment.payment_date or _now()
                if sibling_created_at < latest_package_payment_at or sibling_created_at > payment_created_at:
                    continue
                usage_payments.append((sibling_created_at, sibling_payment_id, sibling_slot, sibling_payment))
        for _, sibling_payment_id, sibling_slot, sibling_payment in sorted(usage_payments, key=lambda row: (row[0], row[1])):
            sibling_remark = str(sibling_payment.remark or "")
            usage_key = (
                ("crt", int(sibling_slot.crt_program_booking_id))
                if sibling_slot.crt_program_booking_id
                else ("slot", int(sibling_slot.id))
            )
            is_usage_charge = "Session completed" in sibling_remark or "Package credit used" in sibling_remark
            if is_usage_charge and usage_key not in charged_usage_keys:
                running_due += float(getattr(sibling_payment, "amount", 0) or 0) or float(sibling_slot.amount or _slot_booking_amount(sibling_slot) or 0)
                charged_usage_keys.add(usage_key)
            running_due -= float(sibling_payment.payment_amount or 0)
            if bool(getattr(sibling_payment, "fully_paid", False)) and "Marked as fully paid" in str(getattr(sibling_payment, "payment_note", "") or ""):
                running_due -= _payment_waived_amount(sibling_payment)
            if sibling_payment_id == int(payment.id or 0):
                return running_due
        return None

    emitted_crt_payment_parent_ids: set[int] = set()
    current_overall_session_due = sum(
        max(0, float(row.due_amount or 0))
        for row in slots
        if not row.crt_program_booking_id
        and not getattr(row, "group_program_booking_id", None)
        and not bool(getattr(row, "is_package_session", False))
        and not bool(getattr(row, "fully_paid", False))
    ) + sum(
        max(0, float(row.due_amount or 0))
        for row in crt_parents
        if not bool(getattr(row, "is_package_session", False))
        and not getattr(row, "patient_package_id", None)
        and not bool(getattr(row, "fully_paid", False))
    )
    for slot_id, payments in payments_by_slot.items():
        slot_booking = slots_by_id.get(slot_id)
        if not slot_booking:
            continue
        if slot_booking.crt_program_booking_id:
            crt_parent_id = int(slot_booking.crt_program_booking_id)
            if crt_parent_id in emitted_crt_payment_parent_ids:
                continue
            emitted_crt_payment_parent_ids.add(crt_parent_id)
            crt_siblings = slots_by_crt_parent.get(crt_parent_id, [slot_booking])
            crt_payment_pairs = [
                (sibling, payment)
                for sibling in crt_siblings
                for payment in payments_by_slot.get(sibling.id, [])
            ]
            cumulative_paid_by_slot: dict[int, float] = defaultdict(float)
            starting_due_by_slot = {
                sibling.id: max(
                    float(sibling.due_amount or 0)
                    + sum(float(payment.payment_amount or 0) for payment in payments_by_slot.get(sibling.id, [])),
                    max(
                        (
                            float(payment.payment_amount or 0)
                            + float(getattr(payment, "due_amount", 0) or 0)
                            for payment in payments_by_slot.get(sibling.id, [])
                        ),
                        default=0,
                    ),
                )
                for sibling in crt_siblings
            }
            for paid_slot, payment in sorted(crt_payment_pairs, key=lambda pair: (pair[1].payment_date or pair[1].created_at or _now(), pair[1].id or 0)):
                previous_due = max(0, starting_due_by_slot.get(paid_slot.id, 0) - cumulative_paid_by_slot[paid_slot.id])
                cumulative_paid_by_slot[paid_slot.id] += float(payment.payment_amount or 0)
                due_override = None
                crt_parent = paid_slot.crt_program_booking
                if crt_parent and not (bool(getattr(crt_parent, "is_package_session", False)) or getattr(crt_parent, "patient_package_id", None)):
                    total = float(crt_parent.total_amount or _slot_booking_amount(paid_slot) or 0)
                    package_covered = float(getattr(crt_parent, "package_covered_amount", 0) or 0)
                    paid_to_date = sum(
                        float(row.payment_amount or 0)
                        for row in payments_by_crt_parent.get(crt_parent_id, [])
                        if (
                            (row.payment_date or row.created_at or _now()),
                            row.id or 0,
                        ) <= (
                            (payment.payment_date or payment.created_at or _now()),
                            payment.id or 0,
                        )
                    )
                    due_override = max(0, total - package_covered - paid_to_date)
                transaction_rows.append(_payment_transaction_shape(
                    payment,
                    slot_booking=paid_slot,
                    cumulative_paid=cumulative_paid_by_slot[paid_slot.id],
                    starting_due=starting_due_by_slot.get(paid_slot.id),
                    previous_due=previous_due,
                    due_override=due_override,
                ))
            continue
        starting_due = max(
            float(slot_booking.due_amount or 0) + sum(float(payment.payment_amount or 0) for payment in payments),
            max((float(payment.payment_amount or 0) + float(getattr(payment, "due_amount", 0) or 0) for payment in payments), default=0),
        )
        cumulative_paid = 0
        for payment in sorted(payments, key=lambda row: (
            (row.payment_date.date() if hasattr(row.payment_date, "date") else row.payment_date) if row.payment_date else None,
            row.id or 0,
        )):
            previous_due = max(0, starting_due - cumulative_paid)
            cumulative_paid += float(payment.payment_amount or 0)
            due_override = None
            transaction_rows.append(_payment_transaction_shape(
                payment,
                slot_booking=slot_booking,
                cumulative_paid=cumulative_paid,
                starting_due=starting_due,
                previous_due=previous_due,
                due_override=due_override,
            ))

    for package_id, payments in payments_by_package.items():
        patient_package = packages_by_id.get(package_id)
        if not patient_package:
            continue
        cumulative_package_paid = package_initial_paid.get(package_id, 0)
        package_created_at = patient_package.created_at or _now()
        def package_payment_order(row: Payment) -> tuple:
            created_at = row.created_at or row.payment_date or _now()
            created_delta = abs((created_at - package_created_at).total_seconds()) if created_at and package_created_at else 0
            return (
                created_delta,
                created_at,
                row.id or 0,
            )

        sorted_package_payments = sorted(payments, key=package_payment_order)
        initial_payment_skipped = False
        for payment in sorted_package_payments:
            if (
                not initial_payment_skipped
                and
                package_initial_paid.get(package_id, 0) > 0
                and abs(float(payment.payment_amount or 0) - float(package_initial_paid.get(package_id, 0))) < 0.01
            ):
                initial_payment_skipped = True
                continue
            cumulative_package_paid += float(payment.payment_amount or 0)
            transaction_rows.append(_payment_transaction_shape(
                payment,
                patient_package=patient_package,
                cumulative_package_paid=cumulative_package_paid,
            ))

    for payment in assessment_payments:
        transaction_rows.append(_payment_transaction_shape(payment))

    def latest_slot_ledger_payment(slot_booking: PatientSlotBooking) -> Optional[Payment]:
        if slot_booking.crt_program_booking_id:
            relevant_payments = payments_by_crt_parent.get(int(slot_booking.crt_program_booking_id), [])
            if not relevant_payments:
                relevant_payments = [
                    payment
                    for sibling in slots_by_crt_parent.get(int(slot_booking.crt_program_booking_id), [slot_booking])
                    for payment in payments_by_slot.get(sibling.id, [])
                ]
        else:
            relevant_payments = payments_by_slot.get(slot_booking.id, [])
        if not relevant_payments:
            return None
        return sorted(
            relevant_payments,
            key=lambda row: (row.created_at or row.payment_date or _now(), row.id or 0),
            reverse=True,
        )[0]

    payable_slot_rows = []
    emitted_payable_crt_parent_ids: set[int] = set()
    emitted_payable_group_child_keys: set[tuple[int, int]] = set()
    excluded_payable_statuses = {"CANCELLED", "UNPAID_CANCELLED"}
    for slot_booking in sorted(slots, key=lambda row: (row.created_at or _now(), row.id or 0)):
        payable_slot = slot_booking
        if slot_booking.crt_program_booking_id:
            crt_parent_id = int(slot_booking.crt_program_booking_id)
            if crt_parent_id in emitted_payable_crt_parent_ids:
                continue
            emitted_payable_crt_parent_ids.add(crt_parent_id)
            crt_siblings = slots_by_crt_parent.get(crt_parent_id, [slot_booking])
            payable_slot = max(
                crt_siblings,
                key=lambda sibling: (
                    bool(sibling.is_package_session),
                    float(sibling.due_amount or 0),
                    float(sibling.amount or _slot_booking_amount(sibling) or 0),
                    int(sibling.id or 0),
                ),
            )
        elif getattr(slot_booking, "group_program_booking_id", None) and slot_booking.patient_id:
            group_child_key = (int(slot_booking.group_program_booking_id), int(slot_booking.patient_id))
            if group_child_key in emitted_payable_group_child_keys:
                continue
            emitted_payable_group_child_keys.add(group_child_key)
            group_siblings = slots_by_group_child.get(group_child_key, [slot_booking])
            payable_slot = max(
                group_siblings,
                key=lambda sibling: (
                    bool(sibling.is_package_session),
                    float(sibling.due_amount or 0),
                    float(sibling.amount or _slot_booking_amount(sibling) or 0),
                    int(sibling.id or 0),
                ),
            )

        if str(payable_slot.status or "").upper() in excluded_payable_statuses:
            continue

        row = _transaction_slot_shape(payable_slot, slot_payments.get(payable_slot.id))
        crt_parent = payable_slot.crt_program_booking
        if crt_parent:
            _sync_crt_parent_payment_from_ledger(db, crt_parent)
            parent_is_package = bool(crt_parent.is_package_session or crt_parent.patient_package_id)
            row["amount"] = float(crt_parent.total_amount or 0)
            row["paid_amount"] = float(crt_parent.paid_amount or 0)
            row["package_covered_amount"] = float(crt_parent.package_covered_amount or 0)
            row["due_amount"] = max(0, float(crt_parent.due_amount or 0))
            row["fully_paid"] = bool(crt_parent.fully_paid) or (not parent_is_package and float(row["due_amount"] or 0) <= 0)
            row["is_package_session"] = parent_is_package
            row["patient_package_id"] = crt_parent.patient_package_id
            row["payment_status"] = crt_parent.payment_status
        else:
            row["paid_amount"] = float(payable_slot.paid_amount or 0)
            row["package_covered_amount"] = float(getattr(payable_slot, "package_covered_amount", 0) or 0)
            slot_amount = float(payable_slot.amount or _slot_booking_amount(payable_slot) or 0)
            row["amount"] = slot_amount
            row["due_amount"] = max(0, float(payable_slot.due_amount or 0))
            if row.get("is_package_session"):
                row["due_amount"] = max(
                    0,
                    slot_amount
                    - float(row.get("package_covered_amount") or 0)
                    - float(row.get("paid_amount") or 0),
                )
            elif getattr(payable_slot, "group_program_booking_id", None):
                row["due_amount"] = max(
                    0,
                    slot_amount
                    - float(row.get("package_covered_amount") or 0)
                    - float(row.get("paid_amount") or 0),
                )
            row["fully_paid"] = bool(getattr(payable_slot, "fully_paid", False))
            row["payment_status"] = payable_slot.payment_status
        latest_payment = latest_slot_ledger_payment(payable_slot)
        if latest_payment and bool(getattr(latest_payment, "fully_paid", False)) and not row.get("is_package_session"):
            row["fully_paid"] = True
        if row.get("is_package_session") and row.get("patient_package_id"):
            if latest_payment and bool(getattr(latest_payment, "fully_paid", False)):
                continue
            row_package = packages_by_id.get(int(row["patient_package_id"]))
            package_due = max(0, float(getattr(row_package, "due_amount", 0) or 0)) if row_package else 0
            if package_due > 0:
                payable_session_amount = _session_payable_due_cap(
                    float(row.get("amount") or 0),
                    float(row.get("paid_amount") or 0),
                )
                row["due_amount"] = min(float(row.get("due_amount") or 0), payable_session_amount)
                row["current_due_amount"] = min(package_due, payable_session_amount)
                row["overall_due_amount"] = package_due
        if row["fully_paid"]:
            continue
        due_amount = float(row.get("current_due_amount", row.get("due_amount")) or 0)
        if due_amount <= 0:
            continue
        row["current_due_amount"] = max(0, float(row.get("current_due_amount", row.get("due_amount")) or 0))
        payable_slot_rows.append(row)

    for crt_parent in sorted(crt_parents, key=lambda row: (row.created_at or _now(), row.id or 0)):
        crt_parent_id = int(crt_parent.id)
        if crt_parent_id in emitted_payable_crt_parent_ids:
            continue
        if str(crt_parent.payment_status or "").upper() in excluded_payable_statuses:
            continue
        _sync_crt_parent_payment_from_ledger(db, crt_parent)
        row = _crt_parent_transaction_shape(
            crt_parent,
            payments_by_crt_parent.get(crt_parent_id, [None])[0],
        )
        if bool(row.get("fully_paid")):
            continue
        due_amount = max(0, float(row.get("due_amount") or 0))
        if row.get("is_package_session") and row.get("patient_package_id"):
            latest_payment = payments_by_crt_parent.get(crt_parent_id, [None])[0]
            if latest_payment and bool(getattr(latest_payment, "fully_paid", False)):
                continue
            row_package = packages_by_id.get(int(row["patient_package_id"]))
            package_due = max(0, float(getattr(row_package, "due_amount", 0) or 0)) if row_package else 0
            if package_due > 0:
                payable_session_amount = _session_payable_due_cap(
                    float(row.get("amount") or 0),
                    float(row.get("paid_amount") or 0),
                )
                due_amount = min(max(due_amount, min(package_due, payable_session_amount)), payable_session_amount)
                row["overall_due_amount"] = package_due
        if due_amount <= 0:
            continue
        row["current_due_amount"] = due_amount
        payable_slot_rows.append(row)

    package_payment_created_at_by_id = {
        str(row.get("id", "")).replace("payment-", ""): row.get("created_at") or ""
        for row in transaction_rows
        if row.get("transaction_type") == "Payment"
        and row.get("patient_package_id")
        and not row.get("patient_slot_booking_id")
        and not row.get("crt_program_booking_id")
    }

    def transaction_sort_key(row: dict) -> tuple:
        session_name = str(row.get("session_name") or "")
        payment_remark = str(row.get("payment_remark") or "")
        is_package_payment = (
            row.get("transaction_type") == "Payment"
            and row.get("patient_package_id")
            and not row.get("patient_slot_booking_id")
            and not row.get("crt_program_booking_id")
        )
        is_package_credit_used = "Package credit used" in session_name or payment_remark.startswith("Allocated from package payment")
        allocated_match = re.search(r"Allocated from package payment\s+(\d+)", payment_remark)
        group_created_at = (
            package_payment_created_at_by_id.get(allocated_match.group(1), row.get("created_at") or "")
            if allocated_match
            else row.get("created_at") or ""
        )
        priority = 2 if is_package_credit_used else (1 if is_package_payment else 0)
        return (group_created_at, priority, row.get("created_at") or "")

    transaction_rows.sort(key=transaction_sort_key, reverse=True)
    payable_slot_rows.sort(key=lambda row: row.get("created_at") or "")
    running_package_due_by_id: dict[int, float] = defaultdict(float)
    for row in payable_slot_rows:
        if not row.get("is_package_session") or not row.get("patient_package_id"):
            continue
        row_package = packages_by_id.get(int(row["patient_package_id"]))
        package_due = max(0, float(getattr(row_package, "due_amount", 0) or 0)) if row_package else 0
        package_rate = _package_per_session_rate(row_package) if row_package else float(row.get("amount") or 0)
        if package_due <= 0 or package_rate <= 0:
            continue
        package_id = int(row["patient_package_id"])
        previous_due = running_package_due_by_id[package_id]
        row_due = min(package_rate, max(0, package_due - previous_due))
        next_due = min(package_due, previous_due + row_due)
        running_package_due_by_id[package_id] = next_due
        row["current_due_amount"] = row_due
        row["due_amount"] = next_due
        row["overall_due_amount"] = package_due

    running_session_due = 0.0
    for row in payable_slot_rows:
        if row.get("is_package_session"):
            continue
        row_due = max(0, float(row.get("current_due_amount", row.get("due_amount")) or 0))
        running_session_due += row_due
        row["current_due_amount"] = row_due
        row["due_amount"] = row_due
        row["overall_due_amount"] = running_session_due
    payable_slot_rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)

    transaction_count = len(transaction_rows)
    transaction_start = (page - 1) * limit
    transaction_end = transaction_start + limit
    paged_transaction_rows = transaction_rows[transaction_start:transaction_end]

    response = {
        "data": {
            "patient": _patient_shape(patient),
            "active_package": _package_shape(active_package) if active_package else None,
            "packages": [_package_shape(pkg) for pkg in packages],
            "payable_slots": payable_slot_rows,
            "payable_assessments": [
                _assessment_billing_transaction_shape(billing)
                for billing in assessment_billings
                if not bool(getattr(billing, "fully_paid", False))
                and float(getattr(billing, "due_amount", 0) or 0) > 0
            ],
            "transactions": paged_transaction_rows,
            "transaction_count": transaction_count,
            "transaction_page": page,
            "transaction_limit": limit,
        }
    }
    if normalized_package_due_by_id:
        db.rollback()
        for package_id, due_amount in normalized_package_due_by_id.items():
            db.query(PatientPackage).filter(PatientPackage.id == package_id).update(
                {PatientPackage.due_amount: due_amount},
                synchronize_session=False,
            )
        for payment_id, due_amount in normalized_payment_due_by_id.items():
            db.query(Payment).filter(Payment.id == payment_id).update(
                {Payment.due_amount: due_amount},
                synchronize_session=False,
            )
        db.commit()
    else:
        db.rollback()
    return response


@router.post("/patients/{patient_id}/transactions")
async def create_patient_transaction(
    patient_id: int,
    request: Request,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    patient = _base_query("patients", db, user).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    _ensure_package_billing_schema(db)
    _ensure_assessment_billing_schema(db)

    slot_booking_id = payload.get("patient_slot_booking_id")
    crt_program_booking_id = payload.get("crt_program_booking_id")
    patient_package_id = payload.get("patient_package_id")
    assessment_payment_id = payload.get("assessment_payment_id")
    paid_amount = float(payload.get("paid_amount") or 0)
    fully_paid = bool(payload.get("fully_paid"))
    if paid_amount < 0:
        raise HTTPException(status_code=400, detail="Paid amount cannot be negative")

    def linked_program_slot_siblings(slot_booking: PatientSlotBooking) -> list[PatientSlotBooking]:
        if not slot_booking or not slot_booking.patient_id or slot_booking.crt_program_booking_id:
            return []
        if getattr(slot_booking, "group_program_booking_id", None):
            return (
                db.query(PatientSlotBooking)
                .filter(
                    PatientSlotBooking.id != slot_booking.id,
                    PatientSlotBooking.patient_id == slot_booking.patient_id,
                    PatientSlotBooking.group_program_booking_id == slot_booking.group_program_booking_id,
                )
                .all()
            )
        mapping = slot_booking.therapist_slot_mapping
        if not mapping or not slot_booking.program_id:
            return []
        return (
            db.query(PatientSlotBooking)
            .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
            .filter(
                PatientSlotBooking.id != slot_booking.id,
                PatientSlotBooking.patient_id == slot_booking.patient_id,
                PatientSlotBooking.program_id == slot_booking.program_id,
                TherapistSlotMapping.slot_date == mapping.slot_date,
                TherapistSlotMapping.slot_id == mapping.slot_id,
                PatientSlotBooking.crt_program_booking_id.is_(None),
            )
            .all()
        )

    def mirror_linked_program_slot_payment_state(slot_booking: PatientSlotBooking) -> None:
        for sibling in linked_program_slot_siblings(slot_booking):
            sibling.status = slot_booking.status
            sibling.fully_paid = bool(slot_booking.fully_paid)
            sibling.payment_status = slot_booking.payment_status
            sibling.patient_package_id = slot_booking.patient_package_id
            sibling.is_package_session = bool(slot_booking.is_package_session)
            sibling.package_covered_amount = 0
            sibling.paid_amount = 0
            sibling.due_amount = 0

    def validate_paid_amount(max_allowed: float, label: str) -> None:
        if paid_amount > max_allowed + 0.01:
            raise HTTPException(
                status_code=400,
                detail=f"Paid amount cannot be more than {label} due",
            )

    slot_booking = None
    patient_package = None
    assessment_source_payment = None
    assessment_billing = None
    payment_due_after: Optional[float] = None
    payment_amount_snapshot: Optional[float] = None
    package_credit_before: Optional[dict[tuple[str, int], dict[str, Any]]] = None
    package_waived_due_adjustment = 0.0
    if assessment_payment_id:
        assessment_source_payment = (
            db.query(Payment)
            .filter(
                Payment.id == int(assessment_payment_id),
                Payment.patient_id == patient_id,
                or_(Payment.remark.like("[assessment:%"), Payment.remark.like("[assessment-payment:%")),
            )
            .first()
        )
        if not assessment_source_payment:
            raise HTTPException(status_code=404, detail="Assessment due not found")
        assessment_chain_id = int(assessment_payment_id)
        latest_assessment_due = (
            db.query(Payment)
            .filter(
                Payment.patient_id == patient_id,
                Payment.due_amount > 0,
                or_(
                    Payment.id == assessment_chain_id,
                    Payment.assessment_source_payment_id == assessment_chain_id,
                    Payment.remark.like(f"[assessment-payment:{assessment_chain_id}]%"),
                ),
            )
            .order_by(Payment.payment_date.desc(), Payment.created_at.desc(), Payment.id.desc())
            .first()
        )
        if latest_assessment_due:
            assessment_source_payment = latest_assessment_due
        assessment_chain_id = int(getattr(assessment_source_payment, "assessment_source_payment_id", None) or assessment_chain_id)
        assessment_billing = (
            db.query(PatientAssessmentBilling)
            .filter(
                PatientAssessmentBilling.patient_id == patient_id,
                PatientAssessmentBilling.source_payment_id == assessment_chain_id,
            )
            .first()
        )
        overall_assessment_due = (
            db.query(func.coalesce(func.sum(PatientAssessmentBilling.due_amount), 0))
            .filter(
                PatientAssessmentBilling.patient_id == patient_id,
                PatientAssessmentBilling.due_amount > 0,
            )
            .scalar()
            or 0
        )
        selected_due_before = (
            float(assessment_billing.due_amount or 0)
            if assessment_billing
            else float(getattr(assessment_source_payment, "due_amount", 0) or 0)
        )
        payload_due = float(payload.get("due_amount") or 0)
        if payload_due > selected_due_before:
            selected_due_before = payload_due
        if selected_due_before <= 0:
            raise HTTPException(status_code=400, detail="Assessment has no pending due")
        validate_paid_amount(selected_due_before, "assessment")
        if fully_paid and paid_amount <= 0:
            paid_amount = 0
        overall_due_before = max(float(overall_assessment_due or 0), selected_due_before)
        other_assessment_due = max(0, overall_due_before - selected_due_before)
        selected_due_after = 0 if fully_paid else max(0, selected_due_before - paid_amount)
        payment_due_after = other_assessment_due + selected_due_after
        payment_amount_snapshot = selected_due_before
        if assessment_billing and float(assessment_billing.assessment_amount or 0) > 0:
            payment_amount_snapshot = float(assessment_billing.assessment_amount or 0)
    elif crt_program_booking_id or slot_booking_id:
        if crt_program_booking_id:
            crt_parent = (
                db.query(CrtProgramBooking)
                .options(
                    joinedload(CrtProgramBooking.patient_package).joinedload(PatientPackage.package),
                    joinedload(CrtProgramBooking.slot_bookings).joinedload(PatientSlotBooking.patient_package).joinedload(PatientPackage.package),
                    joinedload(CrtProgramBooking.slot_bookings).joinedload(PatientSlotBooking.patient_session_plan_item),
                    joinedload(CrtProgramBooking.slot_bookings).joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.slot),
                    joinedload(CrtProgramBooking.slot_bookings).joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.therapy),
                )
                .filter(
                    CrtProgramBooking.id == int(crt_program_booking_id),
                    CrtProgramBooking.patient_id == patient_id,
                )
                .first()
            )
            if not crt_parent:
                raise HTTPException(status_code=404, detail="CRT booking not found")
            crt_slots = list(getattr(crt_parent, "slot_bookings", None) or [])
            if not crt_slots:
                raise HTTPException(status_code=404, detail="CRT booking has no slots")
            slot_booking = max(
                crt_slots,
                key=lambda sibling: (
                    bool(sibling.is_package_session),
                    float(sibling.due_amount or 0),
                    float(sibling.amount or _slot_booking_amount(sibling) or 0),
                    int(sibling.id or 0),
                ),
            )
            if crt_parent.patient_package:
                patient_package = crt_parent.patient_package
        else:
            slot_booking = (
                db.query(PatientSlotBooking)
                .options(
                    joinedload(PatientSlotBooking.patient_package).joinedload(PatientPackage.package),
                    joinedload(PatientSlotBooking.crt_program_booking),
                    joinedload(PatientSlotBooking.patient_session_plan_item),
                    joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.slot),
                    joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.therapy),
                )
                .filter(PatientSlotBooking.id == int(slot_booking_id))
                .first()
            )
        if not slot_booking:
            raise HTTPException(status_code=404, detail="Slot booking not found")
        if slot_booking.patient_package:
            patient_package = slot_booking.patient_package
        plan_patient_id = None
        if slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
            plan_patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
        if slot_booking.patient_id != patient_id and plan_patient_id != patient_id:
            raise HTTPException(status_code=400, detail="Slot booking does not belong to this child")
        if getattr(slot_booking, "group_program_booking_id", None) and slot_booking.patient_id:
            group_siblings = (
                db.query(PatientSlotBooking)
                .options(
                    joinedload(PatientSlotBooking.patient_package).joinedload(PatientPackage.package),
                    joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.slot),
                    joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.therapy),
                )
                .filter(
                    PatientSlotBooking.group_program_booking_id == slot_booking.group_program_booking_id,
                    PatientSlotBooking.patient_id == slot_booking.patient_id,
                )
                .all()
            )
            if group_siblings:
                payable_slot = max(
                    group_siblings,
                    key=lambda sibling: (
                        bool(sibling.is_package_session),
                        float(sibling.due_amount or 0),
                        float(sibling.amount or _slot_booking_amount(sibling) or 0),
                        int(sibling.id or 0),
                    ),
                )
                total_paid = sum(float(sibling.paid_amount or 0) for sibling in group_siblings)
                total_package_covered = sum(float(getattr(sibling, "package_covered_amount", 0) or 0) for sibling in group_siblings)
                amount = max(float(sibling.amount or _slot_booking_amount(sibling) or 0) for sibling in group_siblings)
                payable_slot.amount = amount
                payable_slot.paid_amount = total_paid
                payable_slot.package_covered_amount = total_package_covered
                payable_slot.due_amount = max(0, amount - total_paid - total_package_covered)
                payable_slot.fully_paid = bool(payable_slot.fully_paid or (amount > 0 and payable_slot.due_amount <= 0))
                payable_slot.payment_status = "PAID" if payable_slot.fully_paid else ("PARTIAL" if total_paid > 0 or total_package_covered > 0 else "UNPAID")
                for sibling in group_siblings:
                    if sibling.id == payable_slot.id:
                        continue
                    sibling.amount = 0
                    sibling.paid_amount = 0
                    sibling.package_covered_amount = 0
                    sibling.due_amount = 0
                    sibling.fully_paid = payable_slot.fully_paid
                    sibling.payment_status = payable_slot.payment_status
                slot_booking = payable_slot
                db.flush()

        crt_parent = slot_booking.crt_program_booking
        ledger_filters = []
        if crt_parent:
            ledger_filters.append(Payment.crt_program_booking_id == crt_parent.id)
        else:
            ledger_filters.append(Payment.patient_slot_booking_id == slot_booking.id)
            ledger_filters.append(Payment.remark.like(f"[slot:{slot_booking.id}]%"))
        is_non_package_session_payment = not bool(
            getattr(slot_booking, "is_package_session", False)
            or getattr(slot_booking, "patient_package_id", None)
            or (crt_parent and (getattr(crt_parent, "is_package_session", False) or getattr(crt_parent, "patient_package_id", None)))
        )
        booking_amount_snapshot = float(crt_parent.total_amount or 0) if crt_parent else 0
        if booking_amount_snapshot <= 0:
            booking_amount_snapshot = float(payload.get("booking_amount") or 0) or float(slot_booking.amount or _slot_booking_amount(slot_booking) or 0)
        paid_before = float((crt_parent.paid_amount if crt_parent else slot_booking.paid_amount) or 0)
        session_due_cap = _session_payable_due_cap(booking_amount_snapshot, paid_before)
        selected_due_before = (
            float(getattr(crt_parent, "due_amount", 0) or 0)
            if crt_parent
            else float(getattr(slot_booking, "due_amount", 0) or 0)
        )
        payload_due_before = float(payload.get("due_amount") or 0)
        payload_amount_before = float(payload.get("amount") or 0)
        payload_selected_due = payload_due_before or payload_amount_before
        if is_non_package_session_payment:
            if selected_due_before <= 0:
                selected_due_before = payload_selected_due or float(slot_booking.due_amount or 0)
            if selected_due_before <= 0:
                selected_due_before = session_due_cap or float(_slot_booking_amount(slot_booking) or 0)
        else:
            if payload_selected_due > 0:
                selected_due_before = payload_selected_due
            elif selected_due_before <= 0:
                selected_due_before = session_due_cap or float(_slot_booking_amount(slot_booking) or 0)
        validate_paid_amount(selected_due_before, "session")
        if is_non_package_session_payment:
            slot_due_query = db.query(func.coalesce(func.sum(PatientSlotBooking.due_amount), 0)).filter(
                PatientSlotBooking.patient_id == patient_id,
                PatientSlotBooking.crt_program_booking_id.is_(None),
                PatientSlotBooking.group_program_booking_id.is_(None),
                PatientSlotBooking.is_package_session.is_(False),
                PatientSlotBooking.fully_paid.is_(False),
                PatientSlotBooking.due_amount > 0,
            )
            if not crt_parent:
                slot_due_query = slot_due_query.filter(PatientSlotBooking.id != slot_booking.id)
            slot_due_total = slot_due_query.scalar() or 0
            group_due_total = 0.0
            group_due_rows = (
                db.query(PatientSlotBooking)
                .filter(
                    PatientSlotBooking.patient_id == patient_id,
                    PatientSlotBooking.crt_program_booking_id.is_(None),
                    PatientSlotBooking.group_program_booking_id.isnot(None),
                    PatientSlotBooking.is_package_session.is_(False),
                    PatientSlotBooking.fully_paid.is_(False),
                    PatientSlotBooking.due_amount > 0,
                )
                .all()
            )
            grouped_due_rows: dict[tuple[int, int], list[PatientSlotBooking]] = defaultdict(list)
            for due_row in group_due_rows:
                if due_row.id == slot_booking.id:
                    continue
                grouped_due_rows[(int(due_row.group_program_booking_id), int(due_row.patient_id or patient_id))].append(due_row)
            for due_siblings in grouped_due_rows.values():
                payable_due_row = max(
                    due_siblings,
                    key=lambda sibling: (
                        float(sibling.due_amount or 0),
                        float(sibling.amount or _slot_booking_amount(sibling) or 0),
                        int(sibling.id or 0),
                    ),
                )
                group_due_total += max(0, float(payable_due_row.due_amount or 0))

            crt_due_query = db.query(func.coalesce(func.sum(CrtProgramBooking.due_amount), 0)).filter(
                CrtProgramBooking.patient_id == patient_id,
                CrtProgramBooking.is_package_session.is_(False),
                CrtProgramBooking.fully_paid.is_(False),
                CrtProgramBooking.due_amount > 0,
            )
            if crt_parent:
                crt_due_query = crt_due_query.filter(CrtProgramBooking.id != crt_parent.id)
            crt_due_total = crt_due_query.scalar() or 0

            other_session_due = max(0, float(slot_due_total or 0) + float(group_due_total or 0) + float(crt_due_total or 0))
            selected_due_after = 0 if fully_paid else max(0, selected_due_before - paid_amount)
            payment_due_after = other_session_due + selected_due_after
        elif getattr(slot_booking, "is_package_session", False) and getattr(slot_booking, "patient_package_id", None):
            payment_due_after = max(0, selected_due_before - paid_amount)
        else:
            payment_due_after = 0 if fully_paid else max(0, selected_due_before - paid_amount)
        selected_slot_due_after = 0 if fully_paid else max(0, selected_due_before - paid_amount)
        if booking_amount_snapshot <= 0:
            booking_amount_snapshot = float(payload.get("booking_amount") or 0) or float(slot_booking.amount or _slot_booking_amount(slot_booking) or 0) or selected_due_before
        payment_amount_snapshot = booking_amount_snapshot

        if not slot_booking.patient_package and patient_package_id:
            patient_package = (
                db.query(PatientPackage)
                .options(joinedload(PatientPackage.package))
                .filter(
                    PatientPackage.id == int(patient_package_id),
                    PatientPackage.patient_id == patient_id,
                    PatientPackage.deleted_at.is_(None),
                )
                .first()
            )
            if patient_package:
                slot_booking.patient_package_id = patient_package.id
                slot_booking.is_package_session = True
                slot_booking.patient_package = patient_package

        if crt_parent:
            _sync_crt_parent_payment_from_ledger(db, crt_parent, paid_amount)
            if fully_paid:
                crt_parent.due_amount = 0
                crt_parent.fully_paid = True
                crt_parent.payment_status = "PAID"
            if slot_booking.is_package_session and slot_booking.patient_package:
                _recalculate_package_slot_payments(db, slot_booking.patient_package)
                patient_package = slot_booking.patient_package
        elif slot_booking.is_package_session and slot_booking.patient_package:
            patient_package = slot_booking.patient_package
            package_total = float(patient_package.total_amount or (patient_package.package.price if patient_package.package else 0) or 0)
            _set_patient_package_due_from_usage(patient_package)
            patient_package.payment_status = "PAID" if package_total > 0 and patient_package.paid_amount >= package_total else "PARTIAL"
            slot_booking.paid_amount = float(slot_booking.paid_amount or 0) + paid_amount
            amount = _slot_booking_amount(slot_booking)
            _recalculate_package_slot_payments(db, patient_package)
            slot_booking.fully_paid = bool(fully_paid or selected_slot_due_after <= 0)
            if slot_booking.fully_paid:
                existing_covered = min(amount, float(getattr(slot_booking, "package_covered_amount", 0) or 0))
                _set_slot_payment_from_amounts(slot_booking, amount, float(slot_booking.paid_amount or 0), 0, existing_covered)
                package_waived_due_adjustment = max(0.0, float(selected_due_before or 0) - paid_amount)
                _set_patient_package_due_from_usage(patient_package)
        else:
            slot_booking.paid_amount = float(slot_booking.paid_amount or 0) + paid_amount
            amount = _slot_booking_amount(slot_booking)
            slot_booking.fully_paid = bool(slot_booking.fully_paid or fully_paid or (amount > 0 and float(slot_booking.paid_amount or 0) >= amount))
            _recalculate_slot_payment(slot_booking)
            if slot_booking.fully_paid:
                _set_slot_payment_from_amounts(slot_booking, amount, float(slot_booking.paid_amount or 0), 0, 0)
            if getattr(slot_booking, "group_program_booking_id", None) and slot_booking.patient_id:
                group_siblings = (
                    db.query(PatientSlotBooking)
                    .filter(
                        PatientSlotBooking.group_program_booking_id == slot_booking.group_program_booking_id,
                        PatientSlotBooking.patient_id == slot_booking.patient_id,
                    )
                    .all()
                )
                for sibling in group_siblings:
                    if sibling.id == slot_booking.id:
                        continue
                    sibling.amount = 0
                    sibling.paid_amount = 0
                    sibling.package_covered_amount = 0
                    sibling.due_amount = 0
                    sibling.fully_paid = slot_booking.fully_paid
                    sibling.payment_status = slot_booking.payment_status
    elif patient_package_id:
        patient_package = (
            db.query(PatientPackage)
            .options(joinedload(PatientPackage.package))
            .filter(
                PatientPackage.id == int(patient_package_id),
                PatientPackage.patient_id == patient_id,
                PatientPackage.deleted_at.is_(None),
            )
            .first()
        )
        if not patient_package:
            raise HTTPException(status_code=404, detail="Active package not found")
    else:
        raise HTTPException(status_code=400, detail="Select a session, assessment, or active package")

    if patient_package and paid_amount > 0 and not slot_booking:
        package_total = float(patient_package.total_amount or (patient_package.package.price if patient_package.package else 0) or 0)
        _recalculate_package_slot_payments(db, patient_package)
        package_credit_before = _package_credit_snapshot(patient_package)
        package_paid_before = float(patient_package.paid_amount or 0)
        next_paid_amount = package_paid_before + paid_amount
        patient_package.paid_amount = next_paid_amount
        patient_package.payment_status = "PAID" if package_total > 0 and patient_package.paid_amount >= package_total else "PARTIAL"
        payment_due_after = _recalculate_package_due_with_paid_amount(db, patient_package, next_paid_amount)
        patient_package.due_amount = payment_due_after
        payment_amount_snapshot = float(payload.get("amount") or 0) or max(0, package_total - package_paid_before)

    if slot_booking and not patient_package:
        crt_parent_package = getattr(slot_booking.crt_program_booking, "patient_package", None)
        patient_package = slot_booking.patient_package or crt_parent_package

    if slot_booking and patient_package and (
        getattr(slot_booking, "is_package_session", False)
        or getattr(slot_booking.crt_program_booking, "is_package_session", False)
        or getattr(slot_booking.crt_program_booking, "patient_package_id", None)
    ):
        db.flush()
        _sync_patient_package_paid_amount(patient_package)
        _recalculate_package_slot_payments(db, patient_package)
        payment_due_after = _rebuild_patient_package_payment_due_snapshots(patient_package)

    if slot_booking:
        mirror_linked_program_slot_payment_state(slot_booking)

    should_record_transaction = paid_amount > 0 or fully_paid
    if should_record_transaction:
        raw_remark = payload.get("remarks") or payload.get("remark")
        user_note = str(raw_remark).strip() if raw_remark else ""
        if fully_paid and paid_amount <= 0:
            user_note = " - ".join(part for part in [user_note, "Marked as fully paid"] if part)
        elif fully_paid and slot_booking:
            waived_amount = max(0.0, float(selected_due_before or 0) - paid_amount)
            user_note = " - ".join(
                part
                for part in [
                    user_note,
                    "Marked as fully paid",
                    f"Waived Rs {waived_amount:.2f}" if waived_amount > 0 else "",
                ]
                if part
            )
        session_text = "Payment recorded"
        if assessment_source_payment:
            source_label = re.sub(r"^\[[^\]]+\]\s*", "", assessment_source_payment.remark or "Assessment").split(" | Note: ", 1)[0]
            source_label = source_label.replace("Assessment completed - ", "").replace("Payment for ", "").strip() or "Assessment"
            session_text = f"Payment done for {source_label}"
        elif slot_booking:
            slot_label = _transaction_slot_shape(slot_booking).get("session_name") or "session"
            session_text = f"Payment done for {slot_label}"
        elif patient_package:
            package_label = patient_package.package.name if patient_package.package else f"Package #{patient_package.package_id}"
            session_text = f"Payment done for {package_label}"
            if not user_note:
                user_note = "Package credit added"
        stored_payment_due = (
            payment_due_after
            if slot_booking or assessment_source_payment or patient_package
            else float(getattr(patient_package, "due_amount", 0) or 0)
        )
        payment = Payment(
            patient_id=patient_id,
            assessment_source_payment_id=(
                int(getattr(assessment_source_payment, "assessment_source_payment_id", None) or assessment_source_payment.id)
                if assessment_source_payment
                else None
            ),
            patient_slot_booking_id=slot_booking.id if slot_booking and not slot_booking.crt_program_booking_id else None,
            crt_program_booking_id=slot_booking.crt_program_booking_id if slot_booking and slot_booking.crt_program_booking_id else None,
            amount=(payment_amount_snapshot if slot_booking and payment_amount_snapshot is not None else float(payload.get("amount") or 0) or (payment_amount_snapshot if payment_amount_snapshot is not None else paid_amount + float(stored_payment_due or 0))),
            payment_amount=paid_amount,
            payment_mode=_payment_mode_id(db, payload.get("payment_mode")),
            transaction_id=(str(payload.get("transaction_id")).strip() if payload.get("transaction_id") else None),
            payment_status="PAID",
            fully_paid=(
                True
                if assessment_source_payment and fully_paid
                else
                True
                if slot_booking and fully_paid
                else
                bool(payment_due_after is not None and payment_due_after <= 0)
                if assessment_source_payment
                else
                bool(fully_paid)
                if slot_booking
                else str(getattr(patient_package, "payment_status", "") or "").upper() == "PAID"
            ),
            due_amount=stored_payment_due,
            payment_date=_parse_dt(payload.get("payment_date")) if payload.get("payment_date") else _now(),
            remark=(
                f"[assessment-payment:{assessment_source_payment.id}] {session_text}".strip()
                if assessment_source_payment
                else
                f"[slot:{slot_booking.id}] {session_text}".strip()
                if slot_booking
                else f"[package:{patient_package.id}] {session_text}".strip()
            ),
            payment_note=user_note or None,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(payment)
        if slot_booking and slot_booking.crt_program_booking:
            db.flush()
            _sync_crt_parent_payment_from_ledger(db, slot_booking.crt_program_booking)
        if assessment_billing:
            if fully_paid:
                assessment_billing.paid_amount = max(
                    float(assessment_billing.paid_amount or 0) + paid_amount,
                    float(assessment_billing.assessment_amount or 0),
                )
                assessment_billing.due_amount = 0
                assessment_billing.fully_paid = True
                assessment_billing.payment_status = "PAID"
                assessment_billing.updated_by = user.id
                remaining_paid = 0
            else:
                remaining_paid = paid_amount
            pending_billings = (
                db.query(PatientAssessmentBilling)
                .filter(
                    PatientAssessmentBilling.patient_id == patient_id,
                    PatientAssessmentBilling.due_amount > 0,
                )
                .order_by(
                    (PatientAssessmentBilling.id != assessment_billing.id).asc(),
                    PatientAssessmentBilling.completed_at.asc(),
                    PatientAssessmentBilling.id.asc(),
                )
                .all()
            )
            for billing_row in pending_billings:
                if remaining_paid <= 0:
                    break
                row_due = float(billing_row.due_amount or 0)
                applied = min(row_due, remaining_paid)
                billing_row.paid_amount = float(billing_row.paid_amount or 0) + applied
                billing_row.due_amount = max(0, row_due - applied)
                billing_row.fully_paid = float(billing_row.due_amount or 0) <= 0
                billing_row.payment_status = _assessment_billing_status(
                    float(billing_row.assessment_amount or 0),
                    float(billing_row.paid_amount or 0),
                    float(billing_row.due_amount or 0),
                )
                billing_row.updated_by = user.id
                remaining_paid -= applied
        if patient_package:
            db.flush()
            _sync_patient_package_paid_amount(patient_package)
            _recalculate_package_slot_payments(db, patient_package)
            _rebuild_patient_package_payment_due_snapshots(patient_package)
            if slot_booking and fully_paid:
                payment.due_amount = 0

    if slot_booking:
        mirror_linked_program_slot_payment_state(slot_booking)

    db.commit()
    if slot_booking:
        db.refresh(slot_booking)
        if slot_booking.crt_program_booking:
            db.refresh(slot_booking.crt_program_booking)
    return {"data": _transaction_slot_shape(slot_booking) if slot_booking else None}


@router.post("/patients/{patient_id}/documents")
def upload_patient_document(
    patient_id: int,
    request: Request,
    document_type: str = Form("other"),
    title: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    _ensure_patient_profile_schema(db)
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    original_name = file.filename or "document"
    safe_name = _safe_filename(original_name)
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Only these file types are allowed: {', '.join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))}",
        )

    folder = DOCUMENT_UPLOAD_ROOT / "patients" / str(patient_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"{int(datetime.utcnow().timestamp() * 1000)}-{safe_name}"
    target = folder / stored_name

    with target.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_size = target.stat().st_size
    if file_size > MAX_DOCUMENT_UPLOAD_SIZE_BYTES:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=f"Document size must be {settings.MAX_DOCUMENT_UPLOAD_SIZE_MB} MB or less",
        )

    relative_path = f"patients/{patient_id}/{stored_name}"
    row = Document(
        patient_id=patient_id,
        document_type_id=_document_type_id(db, document_type),
        title=title or original_name,
        file_path=relative_path,
        file_size=file_size,
        uploaded_by=user.id,
        description=notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"data": _document_shape(row)}


@router.post("/patients/{patient_id}/photo")
def upload_patient_photo(
    patient_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = _require_user(request, db)
    _ensure_patient_profile_schema(db)
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if file.content_type and file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="Photo must be JPG, PNG, or WEBP")

    original_name = file.filename or "photo"
    safe_name = _safe_filename(original_name)
    folder = DOCUMENT_UPLOAD_ROOT / "patients" / str(patient_id) / "profile"
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"{int(datetime.utcnow().timestamp() * 1000)}-{safe_name}"
    target = folder / stored_name

    with target.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    patient.profile_photo_path = f"patients/{patient_id}/profile/{stored_name}"
    patient.updated_by = user.id
    db.commit()
    db.refresh(patient)
    return {"data": _patient_shape(patient)}


@router.get("/patients/{patient_id}/photo")
def view_patient_photo(
    patient_id: int,
    db: Session = Depends(get_db),
):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    path = _patient_photo_file_path(patient)
    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


@router.get("/documents/{document_id}/download")
def download_document_file(
    document_id: int,
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    path = _document_file_path(document)

    return FileResponse(
        path=str(path),
        filename=document.title or path.name,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )


@router.get("/documents/{document_id}/view")
def view_document_file(
    document_id: int,
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    path = _document_file_path(document)
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    download_url = f"/api/v1/ui/documents/{document.id}/download"
    raw_url = f"/api/v1/ui/documents/{document.id}/inline"
    title = document.title or path.name

    if mime_type.startswith("text/") or mime_type in {"application/xml", "text/xml", "application/json"}:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="utf-8", errors="replace")
        import html
        return HTMLResponse(f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f8fafc; color: #0f172a; }}
    header {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; background: white; border-bottom: 1px solid #e2e8f0; }}
    a {{ color: #2563eb; text-decoration: none; font-weight: 600; }}
    pre {{ margin: 0; padding: 16px; white-space: pre-wrap; word-break: break-word; font: 13px/1.5 Consolas, monospace; }}
  </style>
</head>
<body>
  <header><strong>{html.escape(title)}</strong><a href="{download_url}">Download</a></header>
  <pre>{html.escape(content)}</pre>
</body>
</html>
""")

    if mime_type.startswith("image/") or mime_type == "application/pdf":
        return FileResponse(
            path=str(path),
            filename=title,
            media_type=mime_type,
            headers={"Content-Disposition": f'inline; filename="{title}"'},
        )

    import html
    return HTMLResponse(f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f8fafc; color: #0f172a; }}
    header {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; background: white; border-bottom: 1px solid #e2e8f0; }}
    main {{ padding: 24px; }}
    iframe {{ width: 100%; height: calc(100vh - 100px); border: 1px solid #e2e8f0; background: white; }}
    a {{ color: #2563eb; text-decoration: none; font-weight: 600; margin-left: 12px; }}
  </style>
</head>
<body>
  <header><strong>{html.escape(title)}</strong><span><a href="{raw_url}" target="_blank">Open Raw</a><a href="{download_url}">Download</a></span></header>
  <main><iframe src="{raw_url}" title="{html.escape(title)}"></iframe></main>
</body>
</html>
""")


@router.get("/documents/{document_id}/inline")
def inline_document_file(
    document_id: int,
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    path = _document_file_path(document)
    return FileResponse(
        path=str(path),
        filename=document.title or path.name,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{document.title or path.name}"'},
    )


@router.get("/package-adjustment-preview")
def package_adjustment_preview(
    patient_id: int = Query(...),
    package_id: int = Query(...),
    db: Session = Depends(get_db),
):
    return {"data": _patient_package_preview(db, patient_id, package_id)}


@router.patch("/therapists/{therapist_id}/therapy-mappings")
def sync_therapist_therapy_mappings(
    therapist_id: int,
    payload: dict = Body(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    user = _current_user(request, db) if request else None
    therapist = db.query(Therapist).filter(Therapist.id == therapist_id).first()
    if not therapist:
        raise HTTPException(status_code=404, detail="Therapist not found")

    selected_ids = {
        int(therapy_id)
        for therapy_id in payload.get("therapy_ids", [])
        if str(therapy_id).strip()
    }
    existing = (
        db.query(TherapistTherapyMapping)
        .filter(TherapistTherapyMapping.therapist_id == therapist_id)
        .all()
    )
    existing_by_therapy = {row.therapy_id: row for row in existing}

    for row in existing:
        row.is_active = row.therapy_id in selected_ids

    for therapy_id in selected_ids:
        if therapy_id not in existing_by_therapy:
            db.add(TherapistTherapyMapping(
                therapist_id=therapist_id,
                therapy_id=therapy_id,
                is_active=True,
            ))

    therapist.updated_by = user.id if user else therapist.updated_by
    db.commit()

    rows = (
        db.query(TherapistTherapyMapping)
        .options(joinedload(TherapistTherapyMapping.therapy))
        .filter(TherapistTherapyMapping.therapist_id == therapist_id)
        .order_by(TherapistTherapyMapping.id.asc())
        .all()
    )
    return {"data": [_therapist_therapy_mapping_shape(row) for row in rows]}


@router.get("/{table}")
async def list_rows(
    table: str,
    request: Request,
    filters: str = Query(default="[]"),
    order_by: Optional[str] = None,
    ascending: bool = True,
    limit: Optional[int] = None,
    offset: int = 0,
    single: bool = False,
    count: bool = False,
    db: Session = Depends(get_db),
):
    user = _current_user(request, db)
    import json

    parsed_filters = json.loads(filters or "[]")
    if table in {"alerts", "session_assignments", "waitlist_entries"}:
        return {"data": [], "count": 0 if count else None}
    if table == "therapist_availability" and not _table_exists(db, "therapist_availability"):
        leave_filters = []
        for item in parsed_filters:
            field = item.get("field")
            leave_filters.append({
                **item,
                "field": "leave_date" if field == "availability_date" else field,
            })
        query = _base_query("therapist_leaves", db, user)
        query = _apply_filters(query, "therapist_leaves", leave_filters)
        total = query.count() if count else None
        rows = query.order_by(TherapistLeave.therapist_id.asc(), TherapistLeave.leave_date.asc()).all()
        data = [_therapist_leave_availability_shape(row) for row in rows]
        if single:
            return {"data": data[0] if data else None, "count": total}
        return {"data": data, "count": total}
    query = _base_query(table, db, user)
    query = _apply_filters(query, table, parsed_filters)
    total = query.count() if count else None
    query = _apply_order(query, table, order_by, ascending)
    if offset:
        query = query.offset(offset)
    if limit:
        query = query.limit(limit)
    rows = query.all()
    data = [SHAPERS[table](row) for row in rows]
    if single:
        return {"data": data[0] if data else None, "count": total}
    return {"data": data, "count": total}


@router.post("/{table}")
async def create_rows(table: str, request: Request, payload: Any = Body(...), db: Session = Depends(get_db)):
    user = _current_user(request, db)
    items = payload if isinstance(payload, list) else [payload]
    if table == "therapist_availability" and not _table_exists(db, "therapist_availability"):
        user_id = user.id if user else None
        created_rows = []
        for item in items:
            therapist_id = item.get("therapist_id")
            current_therapist = _current_therapist(db, user)
            if current_therapist:
                therapist_id = current_therapist.id
            if not therapist_id:
                raise HTTPException(status_code=400, detail="therapist_id is required")
            availability_date = _parse_date(item.get("availability_date")) or date.today()
            _sync_therapist_leave_from_availability(
                db,
                int(therapist_id),
                availability_date,
                item.get("status") or "available",
                item.get("notes"),
                user_id,
            )
            row = (
                db.query(TherapistLeave)
                .filter(
                    TherapistLeave.therapist_id == int(therapist_id),
                    TherapistLeave.leave_date == availability_date,
                )
                .first()
            )
            if row:
                created_rows.append(row)
        db.commit()
        data = [_therapist_leave_availability_shape(row) for row in created_rows]
        return {"data": data[0] if len(data) == 1 else data}
    created = []
    for item in items:
        created.append(_create_one(table, item, db, user))
    db.commit()
    data = [SHAPERS[table](row) for row in created if row is not None and table in SHAPERS]
    return {"data": data[0] if len(data) == 1 else data}


@router.patch("/{table}")
async def update_rows(table: str, request: Request, payload: dict = Body(...), filters: str = Query(default="[]"), db: Session = Depends(get_db)):
    import json

    user = _current_user(request, db)
    parsed_filters = json.loads(filters or "[]")
    if table == "therapist_availability" and not _table_exists(db, "therapist_availability"):
        leave_filters = []
        for item in parsed_filters:
            field = item.get("field")
            value = item.get("value")
            if field == "id" and isinstance(value, int) and value < 0:
                value = abs(value)
            leave_filters.append({
                **item,
                "field": "leave_date" if field == "availability_date" else field,
                "value": value,
            })
        query = _apply_filters(_base_query("therapist_leaves", db, user), "therapist_leaves", leave_filters)
        rows = query.all()
        if _availability_status_requests_leave(payload.get("status")):
            for row in rows:
                row.leave_session = "full_day"
                row.reason = payload.get("notes", row.reason)
                row.updated_by = user.id if user else None
        else:
            for row in rows:
                db.delete(row)
            rows = []
        db.commit()
        data = [_therapist_leave_availability_shape(row) for row in rows]
        return {"data": data[0] if len(data) == 1 else data}
    query = _apply_filters(_base_query(table, db, user), table, parsed_filters)
    rows = query.all()
    for row in rows:
        _update_one(table, row, payload, db, user)
    db.commit()
    data = [SHAPERS[table](row) for row in rows if table in SHAPERS]
    return {"data": data[0] if len(data) == 1 else data}


def _mark_enquiry_converted(db: Session, enquiry_id: Any, patient_id: int, user: Optional[User]) -> None:
    _ensure_enquiry_schema(db)
    try:
        source_id = int(enquiry_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid enquiry id")

    enquiry = db.query(Enquiry).filter(Enquiry.id == source_id, Enquiry.is_active.is_(True)).first()
    if not enquiry:
        raise HTTPException(status_code=404, detail="Enquiry not found")

    region_ids = _user_region_ids(db, user)
    if region_ids and enquiry.region_id not in region_ids:
        raise HTTPException(status_code=403, detail="Enquiry is outside your region")

    enquiry.is_active = False


def _create_one(table: str, item: dict, db: Session, user: Optional[User]):
    if table not in ALLOWED_UI_TABLES:
        raise HTTPException(status_code=404, detail=f"Unsupported table: {table}")

    if table == "patients":
        _ensure_patient_profile_schema(db)
    if table == "enquiries" or (table == "patients" and item.get("source_enquiry_id")):
        _ensure_enquiry_schema(db)

    region = _default_region(db)
    user_id = user.id if user else None
    region_id = item.get("region_id") or _primary_region_id(db, user) or region.id
    if table == "patients":
        first, last = _split_name(item.get("full_name", ""))
        row = Patient(
            first_name=first,
            last_name=last,
            date_of_birth=_parse_date(item.get("date_of_birth")) or date(2000, 1, 1),
            gender=item.get("gender"),
            email=item.get("email"),
            phone=item.get("phone"),
            father_name=item.get("father_name"),
            mother_name=item.get("mother_name"),
            blood_group=item.get("blood_group"),
            nationality=item.get("nationality") or "Indian",
            address=item.get("address"),
            diagnosis=item.get("diagnosis"),
            notes=item.get("notes"),
            alternate_contact=item.get("emergency_contact"),
            emergency_phone=item.get("emergency_phone"),
            referred_by=item.get("referred_by"),
            registration_at=_parse_dt(item.get("registration_at")) if item.get("registration_at") else _now(),
            clinical_observation=item.get("clinical_observation"),
            is_available=item.get("is_available", True),
            region_id=region_id,
        )
    elif table == "enquiries":
        first_name = (item.get("first_name") or "").strip()
        phone = (item.get("phone") or "").strip()
        program_id = item.get("program_id")
        if not first_name:
            raise HTTPException(status_code=400, detail="First name is required")
        if not phone:
            raise HTTPException(status_code=400, detail="Phone number is required")
        if program_id:
            program = db.query(Program).filter(Program.id == program_id, Program.is_active.is_(True)).first()
            if not program or (program.region_id and program.region_id != region_id):
                raise HTTPException(status_code=400, detail="Selected program is not available for this region")
        row = Enquiry(
            first_name=first_name,
            last_name=(item.get("last_name") or "").strip() or None,
            gender=item.get("gender"),
            phone=phone,
            referred_by=item.get("referred_by"),
            enquiry_source=item.get("enquiry_source"),
            program_id=program_id,
            region_id=region_id,
            is_active=True,
        )
    elif table == "appointments":
        start = _parse_dt(item["scheduled_at"])
        row = Appointment(
            patient_id=item["patient_id"],
            therapist_id=item["therapist_id"],
            start_time=start,
            end_time=start + timedelta(minutes=int(item.get("duration_minutes") or 60)),
            status=item.get("status") or "scheduled",
            notes=item.get("notes"),
            region_id=region_id,
        )
    elif table == "sessions":
        row = TherapySession(
            patient_id=item["patient_id"],
            therapist_id=item["therapist_id"],
            appointment_id=item.get("appointment_id"),
            session_number=item.get("session_number") or 1,
            duration_minutes=item.get("duration_minutes") or 60,
            status=item.get("status") or "scheduled",
            session_date=_parse_date(item.get("session_date")) or date.today(),
            progress_notes=item.get("notes") or item.get("progress_notes"),
            billing_status=item.get("billing_status") or "pending",
        )
    elif table == "patient_packages":
        _ensure_package_billing_schema(db)
        _ensure_package_master_schema(db)
        package = db.query(Package).filter(Package.id == item["package_id"], Package.deleted_at.is_(None)).first()
        if not package:
            raise HTTPException(status_code=404, detail="Package not found")

        total_sessions = int(item.get("total_sessions") or package.total_sessions or 0)
        total_amount = float(item.get("total_amount") if item.get("total_amount") is not None else package.price or 0)
        paid_amount = float(item.get("paid_amount") or 0)
        adjust_today_unpaid = bool(item.get("adjust_today_unpaid_sessions"))
        adjustment_preview = _patient_package_preview(db, item["patient_id"], package.id)
        sessions_to_adjust = adjustment_preview["sessions_adjusted"] if adjust_today_unpaid else 0
        per_session_rate = total_amount / total_sessions if total_sessions > 0 else 0
        due_amount = 0
        payment_status = "PAID" if total_amount > 0 and paid_amount >= total_amount else ("PARTIAL" if paid_amount > 0 else "UNPAID")

        row = PatientPackage(
            patient_id=item["patient_id"],
            package_id=package.id,
            start_date=_parse_date(item.get("start_date")) or date.today(),
            end_date=_parse_date(item.get("end_date")),
            sessions_completed=sessions_to_adjust,
            sessions_remaining=max(0, total_sessions - sessions_to_adjust),
            total_amount=total_amount,
            paid_amount=paid_amount,
            due_amount=due_amount,
            payment_status=payment_status,
            status="completed" if sessions_to_adjust >= total_sessions and total_sessions > 0 else (item.get("status") or "active"),
        )
        db.add(row)
        db.flush()
        package_purchase_payment = Payment(
            patient_id=item["patient_id"],
            amount=total_amount,
            payment_amount=paid_amount,
            payment_mode=_payment_mode_id(db, item.get("payment_mode")) if paid_amount > 0 else None,
            transaction_id=(str(item.get("transaction_id")).strip() if item.get("transaction_id") else None),
            payment_status="PAID" if paid_amount > 0 else "UNPAID",
            fully_paid=payment_status == "PAID",
            due_amount=due_amount,
            payment_date=_parse_dt(item.get("payment_date")) if item.get("payment_date") else _now(),
            remark=f"[package:{row.id}] Payment done for {package.name}",
            payment_note="Package purchased",
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(package_purchase_payment)

        if paid_amount > 0:
            row.paid_amount = paid_amount
            row.due_amount = -paid_amount
            package_purchase_payment.due_amount = row.due_amount

        if adjust_today_unpaid and sessions_to_adjust:
            today_sessions = _today_unpaid_completed_slot_query(db, item["patient_id"]).limit(sessions_to_adjust).all()
            for slot_booking in today_sessions:
                slot_booking.patient_package_id = row.id
                slot_booking.is_package_session = True
                slot_booking.amount = _slot_amount(slot_booking)
                slot_booking.paid_amount = 0
            _recalculate_package_slot_payments(db, row)
            db.flush()
            package_purchase_payment.due_amount = float(row.due_amount or 0)
        return row
    elif table == "session_notes":
        row = SessionNote(
            session_id=item["session_id"],
            note_type=item.get("note_type") or "progress",
            content=item.get("note") or item.get("content") or "",
            created_by=user_id or 1,
        )
    elif table == "session_assignments":
        return None
    elif table == "therapists":
        row = Therapist(
            name=item.get("full_name") or item.get("name") or f"Therapist {int(datetime.utcnow().timestamp())}",
            user_id=item.get("user_id"),
            qualification=item.get("qualification") or item.get("bio"),
            region_id=item.get("region_id") or region_id,
            created_by=user_id,
            updated_by=user_id,
        )
    elif table == "therapist_therapy_mapping":
        therapist_id = item.get("therapist_id")
        therapy_id = item.get("therapy_id")
        if not therapist_id or not therapy_id:
            raise HTTPException(status_code=400, detail="therapist_id and therapy_id are required")
        existing = (
            db.query(TherapistTherapyMapping)
            .filter(
                TherapistTherapyMapping.therapist_id == int(therapist_id),
                TherapistTherapyMapping.therapy_id == int(therapy_id),
            )
            .first()
        )
        if existing:
            existing.is_active = bool(item.get("is_active", True))
            return existing
        row = TherapistTherapyMapping(
            therapist_id=int(therapist_id),
            therapy_id=int(therapy_id),
            is_active=bool(item.get("is_active", True)),
        )
    elif table == "therapist_availability":
        therapist_id = item.get("therapist_id")
        current_therapist = _current_therapist(db, user)
        if current_therapist:
            therapist_id = current_therapist.id
        if not therapist_id:
            raise HTTPException(status_code=400, detail="therapist_id is required")
        availability_date = _parse_date(item.get("availability_date")) or date.today()
        status_value = item.get("status") or "available"
        row = TherapistAvailability(
            therapist_id=therapist_id,
            availability_date=availability_date,
            start_time=_parse_time(item.get("start_time")) or time(9, 0),
            end_time=_parse_time(item.get("end_time")) or time(17, 0),
            break_start=_parse_time(item.get("break_start")),
            break_end=_parse_time(item.get("break_end")),
            status=status_value,
            notes=item.get("notes"),
        )
        _sync_therapist_leave_from_availability(
            db,
            int(therapist_id),
            availability_date,
            status_value,
            item.get("notes"),
            user_id,
        )
    elif table == "therapist_leaves":
        therapist_id = item.get("therapist_id")
        current_therapist = _current_therapist(db, user)
        if current_therapist:
            therapist_id = current_therapist.id
        if not therapist_id:
            raise HTTPException(status_code=400, detail="therapist_id is required")
        leave_date = _parse_date(item.get("leave_date")) or date.today()
        existing_leave = (
            db.query(TherapistLeave)
            .filter(
                TherapistLeave.therapist_id == therapist_id,
                TherapistLeave.leave_date == leave_date,
            )
            .first()
        )
        if existing_leave:
            raise HTTPException(
                status_code=400,
                detail="Leave already exists for this therapist on this date",
            )
        leave_session = item.get("leave_session") or "full_day"
        conflicting_bookings = _active_bookings_for_therapist_leave(
            db,
            int(therapist_id),
            leave_date,
            leave_session,
        )
        if conflicting_bookings:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot apply leave on {leave_date.strftime('%d/%m/%Y')}. "
                    f"This therapist has {len(conflicting_bookings)} booked slot(s). "
                    "Please reassign or cancel those slots first."
                ),
            )
        row = TherapistLeave(
            therapist_id=therapist_id,
            leave_date=leave_date,
            leave_session=leave_session,
            reason=item.get("reason"),
            created_by=user_id,
            updated_by=user_id,
        )
    elif table == "users":
        email_value = (item.get("email") or "").strip() if item.get("email") else None
        if email_value:
            existing_user = (
                db.query(User)
                .filter(User.email == email_value, User.deleted_at.is_(None))
                .first()
            )
            if existing_user:
                raise HTTPException(status_code=400, detail="Email already exists")
        first, last = _split_name(item.get("full_name", ""))
        row = User(
            username=item.get("email"),
            email=email_value,
            hashed_password=hash_password(item.get("password") or "Temp@123456"),
            first_name=first,
            last_name=last,
            phone=item.get("phone"),
            is_active=True,
            is_verified=True,
        )
        db.add(row)
        db.flush()
        db.add(UserRegionMapping(userid=row.id, regionid=region_id, created_by=user_id, updated_by=user_id))
        return row
    elif table == "user_roles":
        row = UserRole(user_id=item["user_id"], role_id=item["role_id"])
    elif table == "invoices":
        status_value = {"sent": "issued", "partial": "issued"}.get(item.get("status"), item.get("status") or "draft")
        total = item.get("total") or item.get("total_amount") or item.get("subtotal") or 0
        row = Invoice(
            invoice_number=item.get("invoice_number") or f"INV-{int(datetime.utcnow().timestamp())}",
            patient_id=item["patient_id"],
            region_id=region_id,
            issue_date=date.today(),
            due_date=_parse_date(item.get("due_date")) or date.today(),
            total_amount=total,
            paid_amount=item.get("paid_amount") or 0,
            status=status_value,
            description=item.get("notes") or item.get("description"),
        )
    elif table == "payments":
        patient_id = item.get("patient_id")
        if not patient_id and item.get("invoice_id"):
            invoice = db.query(Invoice).filter(Invoice.id == item["invoice_id"]).first()
            patient_id = invoice.patient_id if invoice else None
        if not patient_id:
            raise HTTPException(status_code=400, detail="patient_id is required")
        row = Payment(
            patient_id=patient_id,
            patient_slot_booking_id=item.get("patient_slot_booking_id"),
            crt_program_booking_id=item.get("crt_program_booking_id"),
            amount=float(item.get("amount") or 0),
            payment_amount=item["amount"],
            payment_mode=_payment_mode_id(db, item.get("payment_method")),
            payment_status=item.get("status") or "completed",
            fully_paid=bool(item.get("fully_paid", False)),
            due_amount=float(item.get("due_amount") or 0),
            payment_date=_parse_dt(item.get("payment_date")) if item.get("payment_date") else _now(),
            remark=item.get("notes") or item.get("reference_number"),
            created_by=user_id,
            updated_by=user_id,
        )
    elif table == "alerts":
        row = Alert(
            patient_id=item.get("patient_id"),
            alert_type=item.get("alert_type") or "custom",
            title=item.get("title") or "Alert",
            description=item.get("message") or item.get("description") or "",
            severity={"info": "low", "warning": "medium"}.get(item.get("severity"), item.get("severity") or "low"),
            is_active=not item.get("is_resolved", False),
            metadata_json={"entity_type": item.get("entity_type"), "entity_id": item.get("entity_id")},
        )
    elif table == "documents":
        row = Document(
            patient_id=item.get("patient_id") or 1,
            document_type_id=_document_type_id(db, item.get("doc_type") or item.get("document_type")),
            title=item.get("title"),
            file_path=item.get("file_url") or item.get("file_path"),
            file_size=item.get("file_size") or 0,
            uploaded_by=user_id or 1,
            description=item.get("notes") or item.get("description"),
        )
    elif table == "audit_logs":
        row = AuditLog(
            user_id=item.get("changed_by") or user_id,
            entity_type=item.get("table_name") or item.get("entity_type"),
            entity_id=item.get("record_id") or item.get("entity_id"),
            action=item.get("action"),
            old_values=item.get("old_data"),
            new_values=item.get("new_data"),
        )
    elif table in {"patient_duplicates", "patients_history"}:
        return None
    else:
        raise HTTPException(status_code=404, detail=f"Unsupported table: {table}")
    db.add(row)
    db.flush()
    if table == "patients" and item.get("source_enquiry_id"):
        _mark_enquiry_converted(db, item.get("source_enquiry_id"), row.id, user)
    return row


def _update_one(table: str, row: Any, item: dict, db: Session, user: Optional[User]):
    if table not in ALLOWED_UI_TABLES:
        raise HTTPException(status_code=404, detail=f"Unsupported table: {table}")

    if table == "patients":
        if "full_name" in item:
            row.first_name, row.last_name = _split_name(item["full_name"])
        for key, attr in {
            "phone": "phone",
            "email": "email",
            "father_name": "father_name",
            "mother_name": "mother_name",
            "blood_group": "blood_group",
            "nationality": "nationality",
            "address": "address",
            "diagnosis": "diagnosis",
            "clinical_observation": "clinical_observation",
            "notes": "notes",
            "emergency_contact": "alternate_contact",
            "emergency_phone": "emergency_phone",
            "referred_by": "referred_by",
            "is_available": "is_available",
        }.items():
            if key in item:
                setattr(row, attr, item[key])
        if "date_of_birth" in item:
            row.date_of_birth = _parse_date(item["date_of_birth"]) or row.date_of_birth
        if "gender" in item:
            row.gender = item["gender"]
        if "registration_at" in item:
            row.registration_at = _parse_dt(item["registration_at"]) if item["registration_at"] else row.registration_at
    elif table == "enquiries":
        if "program_id" in item and item["program_id"]:
            program = db.query(Program).filter(Program.id == item["program_id"], Program.is_active.is_(True)).first()
            if not program or (program.region_id and program.region_id != row.region_id):
                raise HTTPException(status_code=400, detail="Selected program is not available for this region")
        for key in [
            "first_name", "last_name", "gender", "phone",
            "referred_by", "enquiry_source", "program_id", "is_active",
        ]:
            if key in item:
                setattr(row, key, item[key])
    elif table == "appointments":
        if "status" in item:
            row.status = item["status"]
        if "notes" in item:
            row.notes = item["notes"]
    elif table == "sessions":
        if "status" in item:
            row.status = item["status"]
        if "billing_status" in item:
            row.billing_status = item["billing_status"]
        if item.get("is_billed") is False:
            row.billing_status = "pending"
        if "therapist_id" in item:
            row.therapist_id = item["therapist_id"]
    elif table == "therapists":
        if "full_name" in item:
            row.name = item["full_name"]
            if row.user:
                row.user.first_name, row.user.last_name = _split_name(item["full_name"])
        if "name" in item:
            row.name = item["name"]
        if "user_id" in item:
            row.user_id = item["user_id"]
        if "region_id" in item:
            row.region_id = item["region_id"]
        if "email" in item and row.user:
            email_value = (item.get("email") or "").strip()
            if email_value:
                existing_user = (
                    db.query(User)
                    .filter(
                        User.email == email_value,
                        User.id != row.user.id,
                        User.deleted_at.is_(None),
                    )
                    .first()
                )
                if existing_user:
                    raise HTTPException(status_code=400, detail="Email already exists")
                row.user.email = email_value
                row.user.username = email_value
        if "phone" in item and row.user:
            row.user.phone = item["phone"]
        for key, attr in {"qualification": "qualification", "bio": "qualification"}.items():
            if key in item:
                setattr(row, attr, item[key])
    elif table == "therapist_therapy_mapping":
        if "is_active" in item:
            row.is_active = bool(item["is_active"])
    elif table == "therapist_availability":
        if "availability_date" in item:
            row.availability_date = _parse_date(item["availability_date"]) or row.availability_date
        for key in ["start_time", "end_time", "break_start", "break_end"]:
            if key in item:
                setattr(row, key, _parse_time(item[key]))
        for key in ["status", "notes"]:
            if key in item:
                setattr(row, key, item[key])
        _sync_therapist_leave_from_availability(
            db,
            int(row.therapist_id),
            row.availability_date,
            row.status,
            row.notes,
            user.id if user else None,
        )
    elif table == "therapist_leaves":
        if "leave_date" in item:
            row.leave_date = _parse_date(item["leave_date"]) or row.leave_date
        for key in ["leave_session", "reason"]:
            if key in item:
                setattr(row, key, item[key])
    elif table == "users":
        if "full_name" in item:
            row.first_name, row.last_name = _split_name(item["full_name"])
        if "email" in item:
            email_value = (item.get("email") or "").strip()
            if email_value:
                existing_user = (
                    db.query(User)
                    .filter(
                        User.email == email_value,
                        User.id != row.id,
                        User.deleted_at.is_(None),
                    )
                    .first()
                )
                if existing_user:
                    raise HTTPException(status_code=400, detail="Email already exists")
            row.email = email_value or row.email
            row.username = email_value or row.username
        if "phone" in item:
            row.phone = item["phone"]
    elif table == "invoices":
        if "paid_amount" in item:
            row.paid_amount = item["paid_amount"]
        if "status" in item:
            row.status = {"sent": "issued", "partial": "issued"}.get(item["status"], item["status"])
        if "due_date" in item:
            row.due_date = _parse_date(item["due_date"]) or row.due_date
    elif table == "alerts":
        if "is_resolved" in item:
            row.is_active = not item["is_resolved"]
    elif table == "documents":
        if "deleted_at" in item:
            db.delete(row)
            return
    elif table == "session_assignments":
        if "unassigned_at" in item:
            row.deleted_at = _parse_dt(item["unassigned_at"])
    else:
        for key, value in item.items():
            if hasattr(row, key):
                setattr(row, key, value)
    row.updated_at = _now() if hasattr(row, "updated_at") else getattr(row, "updated_at", None)
