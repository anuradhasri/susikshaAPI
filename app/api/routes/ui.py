from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
import hashlib
import mimetypes
from pathlib import Path
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
from app.core.security import decode_token, hash_password, verify_password
from app.email_templates.password_reset import build_password_reset_email
from app.models.models import (
    Appointment,
    AuditLog,
    Document,
    DocumentTypeMaster,
    Invoice,
    Package,
    Patient,
    PatientDuplicate,
    PatientPackage,
    PatientSessionPlan,
    PatientSessionPlanItem,
    PatientSlotBooking,
    TherapistSlotMapping,
    PasswordResetToken,
    Payment,
    PaymentModeMaster,
    Region,
    Role,
    Therapist,
    TherapistAvailability,
    TherapistLeave,
    Session as TherapySession,
    SessionNote,
    User,
    UserRegionMapping,
    UserRole,
    MASTER_LOOKUP_DATA,
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
    if _PACKAGE_BILLING_SCHEMA_READY:
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
        "is_package_session": "BOOLEAN NOT NULL DEFAULT 0",
        "amount": "FLOAT NOT NULL DEFAULT 0",
        "paid_amount": "FLOAT NOT NULL DEFAULT 0",
        "due_amount": "FLOAT NOT NULL DEFAULT 0",
        "payment_status": "VARCHAR(50) NOT NULL DEFAULT 'UNPAID'",
    })
    db.commit()
    _PACKAGE_BILLING_SCHEMA_READY = True


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
    if not any(char.isupper() for char in password):
        return "Password must include at least one uppercase letter"
    if not any(char.isdigit() for char in password):
        return "Password must include at least one number"
    if not any(not char.isalnum() for char in password):
        return "Password must include at least one special character"
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


def _full_name(user: Optional[User]) -> str:
    if not user:
        return "Unknown"
    return f"{user.first_name} {user.last_name}".strip()


def _user_region_ids(db: Session, user: Optional[User]) -> list[int]:
    if not user:
        return []
    return [
        region_id
        for (region_id,) in (
            db.query(UserRegionMapping.regionid)
            .filter(UserRegionMapping.userid == user.id)
            .all()
        )
    ]


def _primary_region_id(db: Session, user: Optional[User]) -> Optional[int]:
    region_ids = _user_region_ids(db, user)
    return region_ids[0] if region_ids else None


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "User", "Account"
    if len(parts) == 1:
        return parts[0], "Account"
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


def _user_shape(user: User, db: Session) -> dict:
    region_ids = _user_region_ids(db, user)
    return {
        "id": user.id,
        "username": user.username,
        "full_name": _full_name(user),
        "email": user.email,
        "phone": user.phone,
        "region_id": region_ids[0] if region_ids else None,
        "region_ids": region_ids,
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


def _current_therapist(db: Session, user: Optional[User]) -> Optional[Therapist]:
    return None


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
        "region_id": patient.region_id,
        "created_at": _iso(patient.created_at),
        "updated_at": _iso(patient.updated_at),
        "deleted_at": None,
        "created_by": patient.created_by,
        "updated_by": patient.updated_by,
        "assessment_answers": patient.assessment_answers,
    }


def _therapist_shape(therapist: Therapist) -> dict:
    return {
        "id": therapist.id,
        "user_id": None,
        "specialization": therapist.qualification,
        "license_number": None,
        "bio": therapist.qualification,
        "max_patients": 30,
        "region_id": therapist.region_id,
        "is_active": True,
        "is_available": True,
        "created_at": _iso(therapist.created_at),
        "updated_at": _iso(therapist.updated_at),
        "deleted_at": None,
        "created_by": therapist.created_by,
        "updated_by": therapist.updated_by,
        "users": {
            "full_name": therapist.name,
            "email": None,
            "phone": None,
        },
        "user": None,
    }


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
    return {
        "id": row.id,
        "therapist_id": row.therapist_id,
        "availability_date": _iso(row.availability_date),
        "start_time": _iso(row.start_time),
        "end_time": _iso(row.end_time),
        "break_start": _iso(row.break_start),
        "break_end": _iso(row.break_end),
        "available_minutes": _availability_minutes(row),
        "status": row.status,
        "notes": row.notes,
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
    completed = patient_package.sessions_completed or 0
    scheduled = 0
    remaining = patient_package.sessions_remaining if patient_package.sessions_remaining is not None else max(0, total - completed)
    return {
        "total": total,
        "completed": completed,
        "scheduled": scheduled,
        "remaining": remaining,
        "sessions": package_sessions,
    }


def _today_unpaid_completed_slot_query(db: Session, patient_id: int):
    _ensure_package_billing_schema(db)
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
    if slot_booking.amount:
        return float(slot_booking.amount or 0)
    plan_item = slot_booking.patient_session_plan_item
    return float(plan_item.amount_per_session or 0) if plan_item else 0


def _patient_package_preview(db: Session, patient_id: int, package_id: int) -> dict:
    _ensure_package_master_schema(db)
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


def _package_utilized_amount(patient_package: PatientPackage) -> float:
    return float(patient_package.sessions_completed or 0) * _package_per_session_rate(patient_package)


def _package_due_amount(patient_package: PatientPackage) -> float:
    return _package_utilized_amount(patient_package) - float(patient_package.paid_amount or 0)


def _package_paid_balance_amount(patient_package: PatientPackage) -> float:
    db = object_session(patient_package)
    if not db or not patient_package.id:
        return float(patient_package.paid_amount or 0)

    billable_status_ids = [
        MASTER_LOOKUP_DATA["patient_slot_booking"]["COMPLETED"],
        MASTER_LOOKUP_DATA["patient_slot_booking"]["PAID_CANCELLED"],
    ]
    used_amount = 0.0
    slot_bookings = (
        db.query(PatientSlotBooking)
        .options(joinedload(PatientSlotBooking.patient_session_plan_item))
        .filter(
            PatientSlotBooking.patient_package_id == patient_package.id,
            PatientSlotBooking.is_package_session.is_(True),
            PatientSlotBooking.status_id.in_(billable_status_ids),
        )
        .all()
    )
    for slot_booking in slot_bookings:
        used_amount += max(0, float(slot_booking.amount or _slot_amount(slot_booking) or 0) - float(slot_booking.due_amount or 0))
    return max(0, float(patient_package.paid_amount or 0) - used_amount)


def _package_shape(patient_package: PatientPackage) -> dict:
    stats = _package_session_stats(patient_package)
    due_amount = _package_due_amount(patient_package)
    return {
        "id": patient_package.id,
        "patient_id": patient_package.patient_id,
        "package_id": patient_package.package_id,
        "package_name": patient_package.package.name if patient_package.package else None,
        "total_sessions": stats["total"],
        "sessions_used": stats["completed"],
        "sessions_completed": stats["completed"],
        "sessions_remaining": stats["remaining"],
        "remaining_sessions": stats["remaining"],
        "total_amount": float(patient_package.total_amount or 0),
        "paid_amount": float(patient_package.paid_amount or 0),
        "paid_balance_amount": _package_paid_balance_amount(patient_package),
        "utilized_amount": _package_utilized_amount(patient_package),
        "per_session_rate": _package_per_session_rate(patient_package),
        "due_amount": due_amount,
        "payment_status": patient_package.payment_status,
        "status": patient_package.status,
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
        "transaction_id": None,
        "notes": payment.remark,
        "payment_remark": payment.remark,
        "status": status_value.lower(),
        "payment_status": status_value,
        "created_at": _iso(payment.created_at),
        "updated_at": _iso(payment.updated_at),
    }


def _slot_booking_amount(slot_booking: PatientSlotBooking) -> float:
    if slot_booking.amount:
        return float(slot_booking.amount or 0)
    plan_item = slot_booking.patient_session_plan_item
    if plan_item and plan_item.amount_per_session:
        return float(plan_item.amount_per_session or 0)
    patient_package = slot_booking.patient_package
    if patient_package and patient_package.package and patient_package.package.total_sessions:
        return float(patient_package.total_amount or patient_package.package.price or 0) / float(patient_package.package.total_sessions or 1)
    return 0


def _is_package_fully_paid(patient_package: Optional[PatientPackage]) -> bool:
    if not patient_package:
        return False
    total = float(patient_package.total_amount or (patient_package.package.price if patient_package.package else 0) or 0)
    paid = float(patient_package.paid_amount or 0)
    due = max(0, total - paid)
    return total > 0 and paid >= total and due <= 0


def _set_slot_payment_from_amounts(slot_booking: PatientSlotBooking, amount: float, paid: float, due: float, package_applied: float = 0) -> None:
    slot_booking.amount = amount
    slot_booking.due_amount = due
    if due <= 0 and amount > 0 and package_applied > 0:
        slot_booking.payment_status = "PACKAGE_COVERED"
    elif paid >= amount and amount > 0:
        slot_booking.payment_status = "PAID"
    elif paid > 0:
        slot_booking.payment_status = "PARTIAL"
    else:
        slot_booking.payment_status = "UNPAID"


def _recalculate_slot_payment(slot_booking: PatientSlotBooking) -> None:
    amount = _slot_booking_amount(slot_booking)
    paid = float(slot_booking.paid_amount or 0)
    package = slot_booking.patient_package
    covered_by_paid_package = bool(slot_booking.is_package_session and package and _is_package_fully_paid(package))
    due = 0 if covered_by_paid_package else max(0, amount - paid)

    _set_slot_payment_from_amounts(slot_booking, amount, paid, due, amount if covered_by_paid_package else 0)


def _recalculate_package_slot_payments(db: Session, patient_package: Optional[PatientPackage]) -> None:
    if not patient_package:
        return

    billable_status_ids = [
        MASTER_LOOKUP_DATA["patient_slot_booking"]["COMPLETED"],
        MASTER_LOOKUP_DATA["patient_slot_booking"]["PAID_CANCELLED"],
    ]
    package_credit = float(patient_package.paid_amount or 0)
    slot_bookings = (
        db.query(PatientSlotBooking)
        .options(
            joinedload(PatientSlotBooking.patient_package).joinedload(PatientPackage.package),
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

    for slot_booking in slot_bookings:
        amount = _slot_booking_amount(slot_booking)
        if slot_booking.status_id not in billable_status_ids:
            _set_slot_payment_from_amounts(slot_booking, amount, float(slot_booking.paid_amount or 0), 0, 0)
            continue
        package_applied = min(package_credit, amount)
        package_credit = max(0, package_credit - package_applied)
        due = max(0, amount - package_applied)
        _set_slot_payment_from_amounts(slot_booking, amount, float(slot_booking.paid_amount or 0), due, package_applied)


def _transaction_slot_shape(slot_booking: PatientSlotBooking, payment: Optional[Payment] = None) -> dict:
    if not slot_booking.is_package_session:
        _recalculate_slot_payment(slot_booking)
    mapping = slot_booking.therapist_slot_mapping
    slot = mapping.slot if mapping else None
    therapy = mapping.therapy if mapping else None
    start = getattr(slot, "start_time", None)
    end = getattr(slot, "end_time", None)
    slot_date = mapping.slot_date if mapping else None
    session_label = "Session"
    therapy_name = getattr(therapy, "therapy_name", None) or getattr(therapy, "name", None)
    if therapy_name:
        session_label = therapy_name
    if start and end:
        session_label = f"{session_label} · {start}-{end}"
    if slot_date:
        session_label = f"{session_label} · {slot_date.strftime('%d/%m/%Y')}"

    return {
        "id": slot_booking.id,
        "transaction_type": "Session Used" if slot_booking.is_package_session else "Direct Payment",
        "patient_slot_booking_id": slot_booking.id,
        "patient_id": slot_booking.patient_id,
        "patient_package_id": slot_booking.patient_package_id,
        "is_package_session": bool(slot_booking.is_package_session),
        "slot_name": getattr(slot, "slot_name", None),
        "session_name": session_label,
        "therapy_name": therapy_name,
        "slot_date": _iso(slot_date),
        "start_time": _iso(start),
        "end_time": _iso(end),
        "amount": float(slot_booking.amount or 0),
        "paid_amount": float(slot_booking.paid_amount or 0),
        "due_amount": float(slot_booking.due_amount or 0),
        "payment_status": slot_booking.payment_status,
        "payment_mode": payment.payment_mode_master.payment_mode_name if payment and payment.payment_mode_master else None,
        "payment_date": _iso(payment.payment_date if payment else None),
        "status": slot_booking.status,
        "created_at": _iso(slot_booking.created_at),
    }


def _package_purchase_transaction_shape(patient_package: PatientPackage, initial_paid_amount: Optional[float] = None) -> dict:
    paid_amount = max(0, float(initial_paid_amount if initial_paid_amount is not None else patient_package.paid_amount or 0))
    return {
        "id": f"package-{patient_package.id}",
        "transaction_type": "Package Purchase",
        "patient_slot_booking_id": None,
        "patient_id": patient_package.patient_id,
        "patient_package_id": patient_package.id,
        "is_package_session": False,
        "slot_name": None,
        "session_name": patient_package.package.name if patient_package.package else f"Package #{patient_package.package_id}",
        "therapy_name": None,
        "slot_date": _iso(patient_package.start_date),
        "start_time": None,
        "end_time": None,
        "amount": float(patient_package.total_amount or 0),
        "paid_amount": paid_amount,
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
) -> dict:
    payment_amount = float(payment.payment_amount or 0)
    if slot_booking:
        base_row = _transaction_slot_shape(slot_booking, payment)
        amount = float(base_row["amount"] or 0)
        due = max(0, amount - cumulative_paid)
        return {
            **base_row,
            "id": f"payment-{payment.id}",
            "transaction_type": "Payment",
            "amount": amount,
            "paid_amount": payment_amount,
            "due_amount": due,
            "payment_status": payment.payment_status or "PAID",
            "payment_mode": payment.payment_mode_master.payment_mode_name if payment.payment_mode_master else None,
            "payment_date": _iso(payment.payment_date or payment.created_at),
            "created_at": _iso(payment.created_at),
        }

    if patient_package:
        amount = float(patient_package.total_amount or 0)
        package_paid_total = float(cumulative_package_paid if cumulative_package_paid is not None else patient_package.paid_amount or 0)
        due_amount = _package_utilized_amount(patient_package) - package_paid_total
        return {
            "id": f"payment-{payment.id}",
            "transaction_type": "Payment",
            "patient_slot_booking_id": None,
            "patient_id": payment.patient_id,
            "patient_package_id": patient_package.id,
            "is_package_session": False,
            "slot_name": None,
            "session_name": patient_package.package.name if patient_package.package else f"Package #{patient_package.package_id}",
            "therapy_name": None,
            "slot_date": _iso(patient_package.start_date),
            "start_time": None,
            "end_time": None,
            "amount": amount,
            "paid_amount": payment_amount,
            "due_amount": due_amount,
            "payment_status": payment.payment_status or "PAID",
            "payment_mode": payment.payment_mode_master.payment_mode_name if payment.payment_mode_master else None,
            "payment_date": _iso(payment.payment_date or payment.created_at),
            "status": patient_package.status,
            "created_at": _iso(payment.created_at),
        }

    return {
        "id": f"payment-{payment.id}",
        "transaction_type": "Payment",
        "patient_slot_booking_id": None,
        "patient_id": payment.patient_id,
        "patient_package_id": None,
        "is_package_session": False,
        "slot_name": None,
        "session_name": payment.remark or "Payment",
        "therapy_name": None,
        "slot_date": None,
        "start_time": None,
        "end_time": None,
        "amount": payment_amount,
        "paid_amount": payment_amount,
        "due_amount": 0,
        "payment_status": payment.payment_status or "PAID",
        "payment_mode": payment.payment_mode_master.payment_mode_name if payment.payment_mode_master else None,
        "payment_date": _iso(payment.payment_date or payment.created_at),
        "status": payment.payment_status or "PAID",
        "created_at": _iso(payment.created_at),
    }


def _payment_mode_id(db: Session, value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)

    mode_name = str(value).strip()
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


SHAPERS = {
    "users": _user_shape_from_model,
    "roles": _role_shape,
    "user_roles": _user_role_shape,
    "patients": _patient_shape,
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
}


ALLOWED_UI_TABLES = {
    "users",
    "roles",
    "user_roles",
    "patients",
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

    if table in {"patients", "patient_packages", "invoices", "payments", "documents"}:
        _ensure_patient_profile_schema(db)

    region_ids = _user_region_ids(db, user)
    roles = _user_roles(db, user)
    current_therapist = _current_therapist(db, user) if "therapist" in roles else None
    if table == "patients":
        query = db.query(Patient)
        if region_ids:
            query = query.filter(Patient.region_id.in_(region_ids))
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
        query = db.query(Therapist)
        if current_therapist:
            query = query.filter(Therapist.id == current_therapist.id)
        elif region_ids:
            query = query.filter(Therapist.region_id.in_(region_ids))
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
        _ensure_package_master_schema(db)
        query = db.query(Package).filter(
            Package.deleted_at.is_(None),
            Package.is_active.is_(True),
            Package.total_sessions.in_([12, 16]),
        )
        if region_ids:
            query = query.filter(Package.region_id.in_(region_ids))
        return query
    if table == "patient_packages":
        _ensure_package_billing_schema(db)
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
        "therapists": Therapist,
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
            expression = func.concat(Patient.first_name, " ", Patient.last_name)
            if op == "ilike":
                query = query.filter(expression.ilike(value.replace("*", "%")))
            elif op == "eq":
                query = query.filter(expression == value)
        elif field == "is_active" and table == "therapists":
            continue
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    tokens = AuthService.create_tokens(user, db)
    AuthService.update_last_login(db, user.id)
    return {"data": {"session": tokens, "user": _user_shape(user, db)}}


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
    old_password = payload.get("old_password")
    new_password = payload.get("new_password")

    if not old_password or not new_password:
        raise HTTPException(status_code=400, detail="Old password and new password are required")
    password_error = _password_policy_error(new_password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)
    if old_password == new_password:
        raise HTTPException(status_code=400, detail="New password must be different from old password")
    if not verify_password(old_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Old password is incorrect")

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
    current_therapist = _current_therapist(db, user) if "therapist" in roles else None
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


@router.get("/analytics")
async def analytics(request: Request, period: Optional[str] = None, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    return {"data": _analytics_payload(db, user, period)}


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
    db: Session = Depends(get_db),
):
    user = _current_user(request, db)
    patient = _base_query("patients", db, user).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    _ensure_package_billing_schema(db)

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
        if slot_booking.is_package_session and slot_booking.patient_package:
            continue
        _recalculate_slot_payment(slot_booking)
    for patient_package in packages:
        _recalculate_package_slot_payments(db, patient_package)
    db.commit()

    slot_payments: dict[int, Payment] = {}
    payments_by_slot: dict[int, list[Payment]] = defaultdict(list)
    payments_by_package: dict[int, list[Payment]] = defaultdict(list)
    payment_rows = (
        db.query(Payment)
        .options(joinedload(Payment.payment_mode_master))
        .filter(Payment.patient_id == patient_id, or_(Payment.remark.like("[slot:%"), Payment.remark.like("[package:%")))
        .order_by(Payment.payment_date.desc(), Payment.created_at.desc(), Payment.id.desc())
        .all()
    )
    for payment in payment_rows:
        remark = payment.remark or ""
        if "]" not in remark:
            continue
        marker = remark.split("]", 1)[0]
        if marker.startswith("[slot:"):
            try:
                slot_id = int(marker.replace("[slot:", ""))
            except ValueError:
                continue
            slot_payments.setdefault(slot_id, payment)
            payments_by_slot[slot_id].append(payment)
        elif marker.startswith("[package:"):
            try:
                package_id = int(marker.replace("[package:", ""))
            except ValueError:
                continue
            payments_by_package[package_id].append(payment)

    package_payment_totals = {
        package_id: sum(float(payment.payment_amount or 0) for payment in payments)
        for package_id, payments in payments_by_package.items()
    }
    package_initial_paid = {
        pkg.id: max(0, float(pkg.paid_amount or 0) - package_payment_totals.get(pkg.id, 0))
        for pkg in packages
    }

    transaction_rows = []
    transaction_rows.extend(_package_purchase_transaction_shape(pkg, package_initial_paid.get(pkg.id)) for pkg in packages)

    slots_by_id = {slot_booking.id: slot_booking for slot_booking in slots}
    packages_by_id = {pkg.id: pkg for pkg in packages}
    package_running_used: dict[int, float] = {}
    package_paid = {pkg.id: float(pkg.paid_amount or 0) for pkg in packages}
    for slot_booking in sorted(slots, key=lambda row: (row.created_at or _now(), row.id or 0)):
        row = _transaction_slot_shape(slot_booking, slot_payments.get(slot_booking.id))
        if slot_booking.is_package_session and slot_booking.patient_package_id:
            package_id = int(slot_booking.patient_package_id)
            status = str(slot_booking.status or "").upper()
            is_billable = status in {"COMPLETED", "PAID_CANCELLED"}
            amount = float(row["amount"] or 0)
            if is_billable:
                package_running_used[package_id] = package_running_used.get(package_id, 0) + amount
            row["paid_amount"] = 0
            row["due_amount"] = package_running_used.get(package_id, 0) - package_paid.get(package_id, 0)
        elif not slot_booking.is_package_session:
            row["paid_amount"] = 0
            row["due_amount"] = float(row["amount"] or 0)
        transaction_rows.append(row)

    for slot_id, payments in payments_by_slot.items():
        slot_booking = slots_by_id.get(slot_id)
        if not slot_booking:
            continue
        cumulative_paid = 0
        for payment in sorted(payments, key=lambda row: (row.payment_date or row.created_at or _now(), row.id or 0)):
            cumulative_paid += float(payment.payment_amount or 0)
            transaction_rows.append(_payment_transaction_shape(payment, slot_booking=slot_booking, cumulative_paid=cumulative_paid))

    for package_id, payments in payments_by_package.items():
        patient_package = packages_by_id.get(package_id)
        if not patient_package:
            continue
        cumulative_package_paid = package_initial_paid.get(package_id, 0)
        for payment in sorted(payments, key=lambda row: (row.payment_date or row.created_at or _now(), row.id or 0)):
            cumulative_package_paid += float(payment.payment_amount or 0)
            transaction_rows.append(_payment_transaction_shape(
                payment,
                patient_package=patient_package,
                cumulative_package_paid=cumulative_package_paid,
            ))

    transaction_rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)

    return {
        "data": {
            "patient": _patient_shape(patient),
            "active_package": _package_shape(active_package) if active_package else None,
            "packages": [_package_shape(pkg) for pkg in packages],
            "transactions": transaction_rows,
        }
    }


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

    slot_booking_id = payload.get("patient_slot_booking_id")
    patient_package_id = payload.get("patient_package_id")
    paid_amount = float(payload.get("paid_amount") or 0)
    if paid_amount < 0:
        raise HTTPException(status_code=400, detail="Paid amount cannot be negative")

    slot_booking = None
    patient_package = None
    if slot_booking_id:
        slot_booking = (
            db.query(PatientSlotBooking)
            .options(
                joinedload(PatientSlotBooking.patient_package).joinedload(PatientPackage.package),
                joinedload(PatientSlotBooking.patient_session_plan_item),
                joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.slot),
                joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.therapy),
            )
            .filter(PatientSlotBooking.id == int(slot_booking_id))
            .first()
        )
        if not slot_booking:
            raise HTTPException(status_code=404, detail="Slot booking not found")
        plan_patient_id = None
        if slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
            plan_patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
        if slot_booking.patient_id != patient_id and plan_patient_id != patient_id:
            raise HTTPException(status_code=400, detail="Slot booking does not belong to this child")

        if slot_booking.is_package_session and slot_booking.patient_package:
            patient_package = slot_booking.patient_package
            package_total = float(patient_package.total_amount or (patient_package.package.price if patient_package.package else 0) or 0)
            patient_package.paid_amount = min(package_total, float(patient_package.paid_amount or 0) + paid_amount)
            patient_package.due_amount = _package_due_amount(patient_package)
            patient_package.payment_status = "PAID" if package_total > 0 and patient_package.paid_amount >= package_total else "PARTIAL"
            slot_booking.paid_amount = float(slot_booking.paid_amount or 0) + paid_amount
            _recalculate_package_slot_payments(db, patient_package)
        else:
            slot_booking.paid_amount = float(slot_booking.paid_amount or 0) + paid_amount
            _recalculate_slot_payment(slot_booking)
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
        raise HTTPException(status_code=400, detail="Select a session or active package")

    if patient_package and paid_amount > 0 and not slot_booking:
        package_total = float(patient_package.total_amount or (patient_package.package.price if patient_package.package else 0) or 0)
        patient_package.paid_amount = min(package_total, float(patient_package.paid_amount or 0) + paid_amount)
        patient_package.due_amount = _package_due_amount(patient_package)
        patient_package.payment_status = "PAID" if package_total > 0 and patient_package.paid_amount >= package_total else "PARTIAL"
        _recalculate_package_slot_payments(db, patient_package)

    if paid_amount > 0:
        payment = Payment(
            patient_id=patient_id,
            payment_amount=paid_amount,
            payment_mode=_payment_mode_id(db, payload.get("payment_mode")),
            payment_status="PAID",
            payment_date=_parse_dt(payload.get("payment_date")) if payload.get("payment_date") else _now(),
            remark=(
                f"[slot:{slot_booking.id}] {payload.get('remarks') or payload.get('remark') or ''}".strip()
                if slot_booking
                else f"[package:{patient_package.id}] {payload.get('remarks') or payload.get('remark') or ''}".strip()
            ),
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(payment)

    db.commit()
    if slot_booking:
        db.refresh(slot_booking)
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
    _ensure_patient_profile_schema(db)
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


@router.get("/{table}")
async def list_rows(
    table: str,
    request: Request,
    filters: str = Query(default="[]"),
    order_by: Optional[str] = None,
    ascending: bool = True,
    limit: Optional[int] = None,
    single: bool = False,
    count: bool = False,
    db: Session = Depends(get_db),
):
    user = _current_user(request, db)
    import json

    parsed_filters = json.loads(filters or "[]")
    if table in {"alerts", "session_assignments", "waitlist_entries"}:
        return {"data": [], "count": 0 if count else None}
    if table in {"sessions", "patient_packages"}:
        _ensure_sessions_for_appointments(db, user)
    query = _base_query(table, db, user)
    query = _apply_filters(query, table, parsed_filters)
    total = query.count() if count else None
    query = _apply_order(query, table, order_by, ascending)
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
    query = _apply_filters(_base_query(table, db, user), table, parsed_filters)
    rows = query.all()
    for row in rows:
        _update_one(table, row, payload, db, user)
    db.commit()
    data = [SHAPERS[table](row) for row in rows if table in SHAPERS]
    return {"data": data[0] if len(data) == 1 else data}


def _create_one(table: str, item: dict, db: Session, user: Optional[User]):
    if table not in ALLOWED_UI_TABLES:
        raise HTTPException(status_code=404, detail=f"Unsupported table: {table}")

    if table == "patients":
        _ensure_patient_profile_schema(db)

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
            region_id=region_id,
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
        due_amount = (sessions_to_adjust * per_session_rate) - paid_amount
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

        if adjust_today_unpaid and sessions_to_adjust:
            today_sessions = _today_unpaid_completed_slot_query(db, item["patient_id"]).limit(sessions_to_adjust).all()
            for slot_booking in today_sessions:
                slot_booking.patient_package_id = row.id
                slot_booking.is_package_session = True
                slot_booking.amount = _slot_amount(slot_booking)
                slot_booking.paid_amount = 0
            _recalculate_package_slot_payments(db, row)
            db.flush()
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
            qualification=item.get("bio"),
            region_id=region_id,
            created_by=user_id,
            updated_by=user_id,
        )
    elif table == "therapist_availability":
        therapist_id = item.get("therapist_id")
        current_therapist = _current_therapist(db, user)
        if current_therapist:
            therapist_id = current_therapist.id
        if not therapist_id:
            raise HTTPException(status_code=400, detail="therapist_id is required")
        row = TherapistAvailability(
            therapist_id=therapist_id,
            availability_date=_parse_date(item.get("availability_date")) or date.today(),
            start_time=_parse_time(item.get("start_time")) or time(9, 0),
            end_time=_parse_time(item.get("end_time")) or time(17, 0),
            break_start=_parse_time(item.get("break_start")),
            break_end=_parse_time(item.get("break_end")),
            status=item.get("status") or "available",
            notes=item.get("notes"),
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
        row = TherapistLeave(
            therapist_id=therapist_id,
            leave_date=leave_date,
            leave_session=item.get("leave_session") or "full_day",
            reason=item.get("reason"),
            created_by=user_id,
            updated_by=user_id,
        )
    elif table == "users":
        first, last = _split_name(item.get("full_name", ""))
        row = User(
            username=item.get("email"),
            email=item.get("email"),
            hashed_password=hash_password(item.get("password") or "Temp@123456"),
            first_name=first,
            last_name=last,
            phone=item.get("phone"),
            region_id=region_id,
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
            payment_amount=item["amount"],
            payment_mode=_payment_mode_id(db, item.get("payment_method")),
            payment_status=item.get("status") or "completed",
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
        }.items():
            if key in item:
                setattr(row, attr, item[key])
        if "date_of_birth" in item:
            row.date_of_birth = _parse_date(item["date_of_birth"]) or row.date_of_birth
        if "gender" in item:
            row.gender = item["gender"]
        if "registration_at" in item:
            row.registration_at = _parse_dt(item["registration_at"]) if item["registration_at"] else row.registration_at
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
        for key, attr in {"specialization": "qualification", "bio": "qualification"}.items():
            if key in item:
                setattr(row, attr, item[key])
    elif table == "therapist_availability":
        if "availability_date" in item:
            row.availability_date = _parse_date(item["availability_date"]) or row.availability_date
        for key in ["start_time", "end_time", "break_start", "break_end"]:
            if key in item:
                setattr(row, key, _parse_time(item[key]))
        for key in ["status", "notes"]:
            if key in item:
                setattr(row, key, item[key])
    elif table == "therapist_leaves":
        if "leave_date" in item:
            row.leave_date = _parse_date(item["leave_date"]) or row.leave_date
        for key in ["leave_session", "reason"]:
            if key in item:
                setattr(row, key, item[key])
    elif table == "users":
        if "full_name" in item:
            row.first_name, row.last_name = _split_name(item["full_name"])
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
