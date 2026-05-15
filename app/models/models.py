from datetime import datetime
from typing import Optional
from sqlalchemy import (
    DECIMAL, BigInteger, Column, String, Integer, Boolean, DateTime, ForeignKey, Float, Text,
    Date, Time, JSON, UniqueConstraint, Index, select
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.core.database import Base


class UserRoleEnum(str, enum.Enum):
    ADMIN = "admin"
    THERAPIST = "therapist"
    FRONT_OFFICE = "front_office"


class DocumentTypeEnum(str, enum.Enum):
    ASSESSMENT = "assessment"
    PROGRESS_REPORT = "progress_report"
    CONSENT_FORM = "consent_form"
    EVALUATION = "evaluation"
    OTHER = "other"


STATUS_MASTER_DATA = {
    "invoice": {
        "draft": 301,
        "issued": 302,
        "overdue": 303,
        "paid": 304,
        "cancelled": 305,
    },
    "patient_session_plan": {
        "ACTIVE": 401,
        "CANCELLED": 402,
        "COMPLETED": 403,
    },
    "patient_assessment": {
        "PENDING": 501,
        "IN_PROGRESS": 502,
        "COMPLETED": 503,
        "CANCELLED": 504,
    },
    "patient_slot_booking": {
        "BOOKED": 601,
        "CANCELLED": 602,
        "COMPLETED": 603,
        "NO_SHOW": 604,
    },
    "patient_therapy": {
        "ACTIVE": 701,
        "COMPLETED": 702,
        "ON_HOLD": 703,
        "CANCELLED": 704,
    },
    "therapist_slot_mapping": {
        "ASSIGNED": 801,
        "BOOKED": 802,
        "COMPLETED": 803,
        "CANCELLED": 804,
    },
    "assessment_type": {
        "STRUCTURED": 901,
        "UNSTRUCTURED": 902,
    },
    "question_type": {
        "TEXT": 1001,
        "MCQ": 1002,
        "FILE_UPLOAD": 1003,
        "RATING": 1004,
        "YES_NO": 1005,
    },
}

STATUS_ID_TO_CODE = {
    category: {status_id: code for code, status_id in values.items()}
    for category, values in STATUS_MASTER_DATA.items()
}


def _coerce_status_code(value):
    if hasattr(value, "value"):
        value = value.value
    return str(value)


def _status_id_for(category: str, value, default: Optional[str] = None) -> Optional[int]:
    if value is None:
        value = default
    if value is None:
        return None

    code = _coerce_status_code(value)
    statuses = STATUS_MASTER_DATA[category]
    if code in statuses:
        return statuses[code]

    for existing_code, status_id in statuses.items():
        if existing_code.lower() == code.lower():
            return status_id

    raise ValueError(f"Unknown {category} status: {value}")


class StatusMaster(Base):
    __tablename__ = "status_master"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(100), nullable=False)
    code = Column(String(100), nullable=False)
    name = Column(String(100), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("category", "code", name="uq_status_master_category_code"),
        Index("idx_status_master_category", "category"),
        Index("idx_status_master_active", "is_active"),
    )


class StatusIdMixin:
    _status_category = None
    _default_status = None

    @hybrid_property
    def status(self):
        if self.status_master is not None:
            return self.status_master.code
        return STATUS_ID_TO_CODE.get(self._status_category, {}).get(self.status_id)

    @status.setter
    def status(self, value):
        self.status_id = _status_id_for(self._status_category, value, self._default_status)

    @status.expression
    def status(cls):
        return (
            select(StatusMaster.code)
            .where(StatusMaster.id == cls.status_id)
            .scalar_subquery()
        )


# ============== USER MANAGEMENT ==============

class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    users = relationship("UserRole", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    phone = Column(String(20), nullable=True)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # region = relationship("Region", back_populates="users")
    user_roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")

    # __table_args__ = (
    #     Index("idx_user_region_id", "region_id"),
    #     Index("idx_user_email_region_id", "email", "region_id"),
    # )


class UserRole(Base):
    __tablename__ = "user_roles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="user_roles")
    role = relationship("Role", back_populates="users")

    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_role"),
        Index("idx_user_role_user_id", "user_id"),
        Index("idx_user_role_role_id", "role_id"),
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User")

    __table_args__ = (
        Index("idx_password_reset_user_id", "user_id"),
        Index("idx_password_reset_active_expires", "is_active", "expires_at"),
    )


class UserRegionMapping(Base):
    __tablename__ = "user_region_mapping"

    id = Column(Integer, primary_key=True, index=True)
    userid = Column(Integer, nullable=False, index=True)
    regionid = Column(Integer, nullable=False, index=True)
    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ============== REGIONS ==============

class Region(Base):
    __tablename__ = "regions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    code = Column(String(10), unique=True, nullable=False)
    location = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # users = relationship("User", back_populates="region")
    patients = relationship("Patient", back_populates="region")
    therapists = relationship("Therapist", back_populates="region")
    invoices = relationship("Invoice", back_populates="region")


# ============== PATIENTS ==============

class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    gender = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)
    address = Column(Text, nullable=True)
    father_name = Column(String(255), nullable=True)
    mother_name = Column(String(255), nullable=True)
    blood_group = Column(String(20), nullable=True)
    nationality = Column(String(100), nullable=True)
    emergency_phone = Column(String(20), nullable=True)
    referred_by = Column(String(255), nullable=True)
    registration_at = Column(DateTime(timezone=True), nullable=True)
    profile_photo_path = Column(String(500), nullable=True)
    diagnosis = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    alternate_contact = Column(String(255), nullable=True)
    clinical_observation = Column(Text, nullable=True)
    status = Column(Boolean, default=True)
    is_available = Column(Boolean, default=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)
    assessment_answers = Column(JSON, nullable=True)  

    region = relationship("Region", back_populates="patients")
    patient_packages = relationship("PatientPackage", back_populates="patient")
    invoices = relationship("Invoice", back_populates="patient")
    payments = relationship("Payment", back_populates="patient")
    documents = relationship("Document", back_populates="patient")

    session_plans = relationship(
        "PatientSessionPlan",
        back_populates="patient"
    )

    __table_args__ = (
        Index("idx_patient_region_id", "region_id"),
        Index("idx_patient_email", "email"),
        Index("idx_patient_phone", "phone"),
    )


# ============== THERAPISTS ==============

class Therapist(Base):
    __tablename__ = "therapists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    qualification = Column(Text, nullable=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, nullable=False)
    
    region = relationship("Region", back_populates="therapists")
    availability = relationship("TherapistAvailability", back_populates="therapist")

    __table_args__ = (
        Index("idx_therapist_region_id", "region_id"),
    )

    therapy_mappings = relationship(
        "TherapistTherapyMapping",
        back_populates="therapist"
    )
    
    @property
    def user_id(self):
        return None

    @property
    def license_number(self):
        return None

    @property
    def specialization(self):
        return self.qualification

    @property
    def is_available(self):
        return True


class TherapistAvailability(Base):
    __tablename__ = "therapist_availability"

    id = Column(Integer, primary_key=True, index=True)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False)
    availability_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    break_start = Column(Time, nullable=True)
    break_end = Column(Time, nullable=True)
    status = Column(String(50), default="available")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    therapist = relationship("Therapist", back_populates="availability")

    __table_args__ = (
        UniqueConstraint("therapist_id", "availability_date", name="uq_therapist_availability_date"),
        Index("idx_therapist_availability_therapist_id", "therapist_id"),
        Index("idx_therapist_availability_date", "availability_date"),
        Index("idx_therapist_availability_status", "status"),
    )


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(50), nullable=False, default="scheduled")
    notes = Column(Text, nullable=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient")
    therapist = relationship("Therapist")
    region = relationship("Region")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=True)
    session_number = Column(Integer, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    status = Column(String(50), nullable=False, default="scheduled")
    session_date = Column(Date, nullable=False)
    progress_notes = Column(Text, nullable=True)
    billing_status = Column(String(50), default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient")
    therapist = relationship("Therapist")
    appointment = relationship("Appointment")


class SessionNote(Base):
    __tablename__ = "session_notes"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    note_type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    session = relationship("Session")


# ============== PACKAGES ==============

class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    total_sessions = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    duration_days = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient_packages = relationship("PatientPackage", back_populates="package")


class PatientPackage(Base):
    __tablename__ = "patient_packages"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    sessions_completed = Column(Integer, default=0)
    sessions_remaining = Column(Integer, nullable=False)
    status = Column(String(50), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient", back_populates="patient_packages")
    package = relationship("Package", back_populates="patient_packages")

    __table_args__ = (
        Index("idx_patient_package_patient_id", "patient_id"),
        Index("idx_patient_package_package_id", "package_id"),
        Index("idx_patient_package_status", "status"),
    )


# ============== BILLING ==============

class Invoice(StatusIdMixin, Base):
    __tablename__ = "invoices"
    _status_category = "invoice"
    _default_status = "draft"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(100), unique=True, nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    issue_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False)
    total_amount = Column(Float, nullable=False)
    paid_amount = Column(Float, default=0)
    status_id = Column(Integer, ForeignKey("status_master.id"), nullable=False, default=301, server_default="301")
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient", back_populates="invoices")
    region = relationship("Region", back_populates="invoices")
    invoice_items = relationship("InvoiceItem", back_populates="invoice")
    # payments = relationship("Payment", back_populates="invoice")
    status_master = relationship("StatusMaster", foreign_keys=[status_id])

    __table_args__ = (
        Index("idx_invoice_patient_id", "patient_id"),
        Index("idx_invoice_region_id", "region_id"),
        Index("idx_invoice_status_id", "status_id"),
        Index("idx_invoice_due_date", "due_date"),
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    description = Column(String(255), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    total_price = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    invoice = relationship("Invoice", back_populates="invoice_items")

    __table_args__ = (
        Index("idx_invoice_item_invoice_id", "invoice_id"),
    )


class Payment(Base):
    __tablename__ = "payments"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)

    patient_id = Column(
        Integer,
        ForeignKey("patients.id"),
        nullable=False,
        index=True
    )

    payment_amount = Column(DECIMAL(12, 2), nullable=False)

    payment_status = Column(String(50), nullable=True)

    payment_mode = Column(
        Integer,
        ForeignKey("payment_mode_master.id"),
        nullable=True
    )

    remark = Column(Text, nullable=True)

    payment_date = Column(DateTime, nullable=True)

    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)

    created_at = Column(
        DateTime,
        server_default=func.now()
    )

    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now()
    )

    patient = relationship("Patient", back_populates="payments")

    payment_mode_master = relationship("PaymentModeMaster", back_populates="payments")

    __table_args__ = (
        Index("idx_payment_status", "payment_status"),
    )


# ============== NOTIFICATIONS ==============

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    notification_type = Column(String(50), nullable=False)
    is_read = Column(Boolean, default=False)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_notification_user_id", "user_id"),
        Index("idx_notification_is_read", "is_read"),
    )


# ============== DOCUMENTS ==============

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    document_type_id = Column(Integer, ForeignKey("document_type_master.id"), nullable=False)
    title = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    patient = relationship("Patient", back_populates="documents")

    __table_args__ = (
        Index("idx_document_patient_id", "patient_id"),
        Index("idx_document_type", "document_type_id"),
    )


class DocumentTypeMaster(Base):
    __tablename__ = "document_type_master"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ============== AUDIT LOGS ==============

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(Integer, nullable=False)
    action = Column(String(50), nullable=False)  # CREATE, UPDATE, DELETE, VIEW
    old_values = Column(JSON, nullable=True)
    new_values = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_audit_user_id", "user_id"),
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_created_at", "created_at"),
    )


# ============== DUPLICATE DETECTION ==============

class PatientDuplicate(Base):
    __tablename__ = "patient_duplicates"

    id = Column(Integer, primary_key=True, index=True)
    patient_id_1 = Column(Integer, ForeignKey("patients.id"), nullable=False)
    patient_id_2 = Column(Integer, ForeignKey("patients.id"), nullable=False)
    similarity_score = Column(Float, nullable=False)  # 0-1
    matched_fields = Column(JSON, nullable=False)  # Which fields matched
    status = Column(String(50), default="pending")  # pending, reviewed, merged, rejected
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_patient_duplicate_patient_id_1", "patient_id_1"),
        Index("idx_patient_duplicate_patient_id_2", "patient_id_2"),
        Index("idx_patient_duplicate_status", "status"),
    )

class PaymentModeMaster(Base):
    __tablename__ = "payment_mode_master"

    id = Column(Integer, primary_key=True, index=True)

    payment_mode_name = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    payments = relationship(
        "Payment",
        back_populates="payment_mode_master"
    )
    
class SlotMaster(Base):
    __tablename__ = "slot_master"

    id = Column(Integer, primary_key=True, index=True)
    slot_name = Column(String(100), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    duration_minutes = Column(Integer, nullable = False)
    is_active = Column(Boolean, nullable=False, default=True)

class PatientSessionPlan(StatusIdMixin, Base):
    __tablename__ = "patient_session_plan"
    _status_category = "patient_session_plan"
    _default_status = "ACTIVE"

    id = Column(Integer, primary_key=True, index=True)

    patient_id = Column(
        Integer,
        ForeignKey("patients.id"),
        nullable=False,
        index=True
    )

    plan_name = Column(String(255), nullable=False)

    total_sessions = Column(Integer, nullable=True)

    start_date = Column(Date, nullable = True)
    
    end_date = Column(Date, nullable = True)
    
    status_id = Column(Integer, ForeignKey("status_master.id"), nullable=False, default=401, server_default="401")

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

    # Relationship
    patient = relationship("Patient", back_populates="session_plans")
    status_master = relationship("StatusMaster", foreign_keys=[status_id])
    
class PatientSessionPlanItem(Base):
    __tablename__ = "patient_session_plan_item"

    id = Column(Integer, primary_key=True, index=True)

    patient_session_plan_id = Column(
        Integer,
        ForeignKey("patient_session_plan.id"),
        nullable=False
    )

    therapy_id = Column(
        Integer,
        ForeignKey("therapy_master.id"),
        nullable=False
    )   

    allocated_sessions = Column(Integer, nullable=False)
    assigned_sessions = Column(Integer, nullable=False, default=0)
    completed_sessions = Column(Integer, nullable=False, default=0)
    
    therapy = relationship(
        "TherapyMaster",
        back_populates="patient_session_plan_items"
    )

    patient_slot_bookings = relationship(
        "PatientSlotBooking",
        back_populates="patient_session_plan_item"
    )
    
class TherapyMaster(Base):
    __tablename__ = "therapy_master"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), nullable=False)

    therapy_code = Column(String(100), unique=True, nullable=True)

    is_active = Column(Boolean, nullable=False)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

    # Relationships
    patient_session_plan_items = relationship(
        "PatientSessionPlanItem",
        back_populates="therapy"
    )    
    
    therapist_mappings = relationship(
        "TherapistTherapyMapping",
        back_populates="therapy"
    )
    
    
class TherapistTherapyMapping(Base):
    __tablename__ = "therapist_therapy_mapping"

    id = Column(Integer, primary_key=True, index=True)

    therapist_id = Column(
        Integer,
        ForeignKey("therapists.id"),
        nullable=False
    )

    therapy_id = Column(
        Integer,
        ForeignKey("therapy_master.id"),
        nullable=False
    )
    is_active = Column(Boolean, nullable=False)
    therapist = relationship(
        "Therapist",
        back_populates="therapy_mappings"
    )

    therapy = relationship(
        "TherapyMaster",
        back_populates="therapist_mappings"
    )


class AssessmentMaster(Base):
    __tablename__ = "assessment_master"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    type_id = Column(Integer, ForeignKey("status_master.id"), nullable=False, default=901, server_default="901")
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_date = Column(DateTime, server_default=func.now())
    updated_date = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(Integer, nullable=True)
    created_by = Column(Integer, nullable=True)

    type_master = relationship("StatusMaster", foreign_keys=[type_id])


class QuestionMaster(Base):
    __tablename__ = "question_master"

    id = Column(Integer, primary_key=True, index=True)
    question_text = Column(Text, nullable=False)
    question_type_id = Column(Integer, ForeignKey("status_master.id"), nullable=False, default=1001, server_default="1001")
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_date = Column(DateTime, server_default=func.now())
    updated_date = Column(DateTime, server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)

    question_type_master = relationship("StatusMaster", foreign_keys=[question_type_id])


class AssessmentQuestion(Base):
    __tablename__ = "assessment_question"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessment_master.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("question_master.id"), nullable=False)
    created_date = Column(DateTime, server_default=func.now())
    updated_date = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column("UPDATED_BY", Integer, nullable=True)
    created_by = Column("CREATED_BY", Integer, nullable=True)


class PatientAssessment(StatusIdMixin, Base):
    __tablename__ = "patient_assessment"
    _status_category = "patient_assessment"
    _default_status = "PENDING"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    assessment_id = Column(Integer, ForeignKey("assessment_master.id"), nullable=False)
    assigned_by = Column(Integer, nullable=True)
    status_id = Column(Integer, ForeignKey("status_master.id"), nullable=False, default=501, server_default="501")
    completed_date = Column("completed_Date", DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_date = Column(DateTime, server_default=func.now())
    updated_date = Column(DateTime, server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)

    status_master = relationship("StatusMaster", foreign_keys=[status_id])


class PatientAssessmentDetail(Base):
    __tablename__ = "patient_assessment_detail"

    id = Column(Integer, primary_key=True, index=True)
    patient_assessment_id = Column(Integer, ForeignKey("patient_assessment.id"), nullable=False)
    assessment_question_id = Column(Integer, ForeignKey("assessment_question.id"), nullable=False)
    answer_text = Column(Text, nullable=True)
    file_path = Column(String(500), nullable=True)
    file_name = Column(String(255), nullable=True)
    file_size = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)


# class PatientSlotBooking(StatusIdMixin, Base):
#     __tablename__ = "patient_slot_booking"
#     _status_category = "patient_slot_booking"
#     _default_status = "BOOKED"

#     id = Column(Integer, primary_key=True, index=True)
#     therapist_slot_mapping_id = Column(Integer, ForeignKey("therapist_slot_mapping.id"), nullable=False)
#     patient_session_plan_item_id = Column(Integer, ForeignKey("patient_session_plan_item.id"), nullable=True)
#     status_id = Column(Integer, ForeignKey("status_master.id"), nullable=False, default=601, server_default="601")
#     created_at = Column(DateTime, server_default=func.now())
#     updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

#     status_master = relationship("StatusMaster", foreign_keys=[status_id])


class PatientTherapy(StatusIdMixin, Base):
    __tablename__ = "patient_therapy"
    _status_category = "patient_therapy"
    _default_status = "ACTIVE"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    therapy_id = Column(Integer, ForeignKey("therapy_master.id"), nullable=False)
    number_of_sessions = Column(Integer, nullable=False, default=0, server_default="0")
    status_id = Column(Integer, ForeignKey("status_master.id"), nullable=True, default=701, server_default="701")
    slot_id = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    created_date = Column(DateTime, server_default=func.now())
    created_by = Column(Integer, nullable=True)
    updated_date = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")

    status_master = relationship("StatusMaster", foreign_keys=[status_id])


class LeaveSession(Base):
    __tablename__ = "leave_sessions"

    id = Column(BigInteger, primary_key=True, index=True)
    code = Column(String(30), nullable=False)
    name = Column(String(50), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class TherapistLeave(Base):
    __tablename__ = "therapist_leaves"

    id = Column(Integer, primary_key=True, index=True)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False)
    leave_date = Column(Date, nullable=False)
    leave_session = Column(String(20), nullable=False, default="full_day")
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)

    therapist = relationship("Therapist")


class TherapistSlotMapping(StatusIdMixin, Base):
    __tablename__ = "therapist_slot_mapping"
    _status_category = "therapist_slot_mapping"
    _default_status = "BOOKED"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False)
    slot_id = Column(Integer, ForeignKey("slot_master.id"), nullable=False)
    slot_date = Column(Date, nullable=False)
    therapy_id = Column(Integer, ForeignKey("therapy_master.id"), nullable=False)
    status_id = Column(Integer, ForeignKey("status_master.id"), nullable=False, default=802, server_default="802")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    therapist = relationship("Therapist")
    slot = relationship("SlotMaster")
    therapy = relationship("TherapyMaster")
    status_master = relationship("StatusMaster", foreign_keys=[status_id])
    patient_slot_bookings = relationship(
        "PatientSlotBooking",
        back_populates="therapist_slot_mapping"
    )


class PatientSlotBooking(StatusIdMixin, Base):
    __tablename__ = "patient_slot_booking"
    _status_category = "patient_slot_booking"
    _default_status = "BOOKED"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    therapist_slot_mapping_id = Column(
        Integer,
        ForeignKey("therapist_slot_mapping.id"),
        nullable=False
    )
    patient_session_plan_item_id = Column(
        Integer,
        ForeignKey("patient_session_plan_item.id"),
        nullable=True
    )
    status_id = Column(Integer, ForeignKey("status_master.id"), nullable=False, default=601, server_default="601")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    status_master = relationship("StatusMaster", foreign_keys=[status_id])
    therapist_slot_mapping = relationship(
        "TherapistSlotMapping",
        back_populates="patient_slot_bookings"
    )
    patient_session_plan_item = relationship(
        "PatientSessionPlanItem",
        back_populates="patient_slot_bookings"
    )
