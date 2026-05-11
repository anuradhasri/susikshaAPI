from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, ForeignKey, Float, Text,
    Enum, Date, Time, JSON, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.core.database import Base


class UserRoleEnum(str, enum.Enum):
    ADMIN = "admin"
    THERAPIST = "therapist"
    FRONT_OFFICE = "front_office"


class AppointmentStatusEnum(str, enum.Enum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class InvoiceStatusEnum(str, enum.Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    OVERDUE = "overdue"
    PAID = "paid"
    CANCELLED = "cancelled"


class PaymentStatusEnum(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class SessionStatusEnum(str, enum.Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class DocumentTypeEnum(str, enum.Enum):
    ASSESSMENT = "assessment"
    PROGRESS_REPORT = "progress_report"
    CONSENT_FORM = "consent_form"
    EVALUATION = "evaluation"
    OTHER = "other"


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
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    phone = Column(String(20), nullable=True)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    region = relationship("Region", back_populates="users")
    user_roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")
    therapist_profile = relationship("Therapist", back_populates="user", uselist=False)

    __table_args__ = (
        Index("idx_user_region_id", "region_id"),
        Index("idx_user_email_region_id", "email", "region_id"),
    )


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

    users = relationship("User", back_populates="region")
    patients = relationship("Patient", back_populates="region")
    therapists = relationship("Therapist", back_populates="region")
    appointments = relationship("Appointment", back_populates="region")
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
    diagnosis = Column(String(255), nullable=True)
    medical_history = Column(Text, nullable=True)
    emergency_contact = Column(String(255), nullable=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    region = relationship("Region", back_populates="patients")
    appointments = relationship("Appointment", back_populates="patient")
    sessions = relationship("Session", back_populates="patient")
    patient_packages = relationship("PatientPackage", back_populates="patient")
    invoices = relationship("Invoice", back_populates="patient")
    documents = relationship("Document", back_populates="patient")
    alerts = relationship("Alert", back_populates="patient")

    __table_args__ = (
        Index("idx_patient_region_id", "region_id"),
        Index("idx_patient_email", "email"),
        Index("idx_patient_phone", "phone"),
    )


# ============== THERAPISTS ==============

class Therapist(Base):
    __tablename__ = "therapists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    license_number = Column(String(100), unique=True, nullable=False)
    specialization = Column(String(255), nullable=True)
    qualification = Column(Text, nullable=True)
    is_available = Column(Boolean, default=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="therapist_profile")
    region = relationship("Region", back_populates="therapists")
    appointments = relationship("Appointment", back_populates="therapist")
    sessions = relationship("Session", back_populates="therapist")
    availability = relationship("TherapistAvailability", back_populates="therapist")

    __table_args__ = (
        Index("idx_therapist_region_id", "region_id"),
        Index("idx_therapist_user_id", "user_id"),
    )


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


# ============== APPOINTMENTS ==============

class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    status = Column(Enum(AppointmentStatusEnum), default=AppointmentStatusEnum.SCHEDULED)
    notes = Column(Text, nullable=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient", back_populates="appointments")
    therapist = relationship("Therapist", back_populates="appointments")
    region = relationship("Region", back_populates="appointments")
    sessions = relationship("Session", back_populates="appointment")

    __table_args__ = (
        Index("idx_appointment_patient_id", "patient_id"),
        Index("idx_appointment_therapist_id", "therapist_id"),
        Index("idx_appointment_region_id", "region_id"),
        Index("idx_appointment_start_time", "start_time"),
        Index("idx_appointment_status", "status"),
    )


# ============== SESSIONS ==============

class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=True)
    session_number = Column(Integer, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    status = Column(Enum(SessionStatusEnum), default=SessionStatusEnum.SCHEDULED)
    session_date = Column(Date, nullable=False)
    progress_notes = Column(Text, nullable=True)
    billing_status = Column(String(50), default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient", back_populates="sessions")
    therapist = relationship("Therapist", back_populates="sessions")
    appointment = relationship("Appointment", back_populates="sessions")
    session_notes = relationship("SessionNote", back_populates="session")
    session_assignments = relationship("SessionAssignment", back_populates="session")

    __table_args__ = (
        Index("idx_session_patient_id", "patient_id"),
        Index("idx_session_therapist_id", "therapist_id"),
        Index("idx_session_date", "session_date"),
        Index("idx_session_status", "status"),
    )


class SessionNote(Base):
    __tablename__ = "session_notes"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    note_type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    session = relationship("Session", back_populates="session_notes")

    __table_args__ = (
        Index("idx_session_note_session_id", "session_id"),
    )


class SessionAssignment(Base):
    __tablename__ = "session_assignments"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    session = relationship("Session", back_populates="session_assignments")
    package = relationship("Package", back_populates="session_assignments")

    __table_args__ = (
        Index("idx_session_assignment_session_id", "session_id"),
        Index("idx_session_assignment_package_id", "package_id"),
    )


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
    session_assignments = relationship("SessionAssignment", back_populates="package")


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

class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(100), unique=True, nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    issue_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False)
    total_amount = Column(Float, nullable=False)
    paid_amount = Column(Float, default=0)
    status = Column(Enum(InvoiceStatusEnum), default=InvoiceStatusEnum.DRAFT)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient", back_populates="invoices")
    region = relationship("Region", back_populates="invoices")
    invoice_items = relationship("InvoiceItem", back_populates="invoice")
    payments = relationship("Payment", back_populates="invoice")

    __table_args__ = (
        Index("idx_invoice_patient_id", "patient_id"),
        Index("idx_invoice_region_id", "region_id"),
        Index("idx_invoice_status", "status"),
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

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    amount = Column(Float, nullable=False)
    payment_method = Column(String(50), nullable=False)
    transaction_id = Column(String(100), nullable=True)
    status = Column(Enum(PaymentStatusEnum), default=PaymentStatusEnum.PENDING)
    payment_date = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    invoice = relationship("Invoice", back_populates="payments")

    __table_args__ = (
        Index("idx_payment_invoice_id", "invoice_id"),
        Index("idx_payment_status", "status"),
    )


# ============== NOTIFICATIONS & ALERTS ==============

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


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    alert_type = Column(String(100), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    severity = Column(String(20), nullable=False)  # low, medium, high, critical
    is_active = Column(Boolean, default=True)
    metadata_json = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient", back_populates="alerts")

    __table_args__ = (
        Index("idx_alert_patient_id", "patient_id"),
        Index("idx_alert_type", "alert_type"),
        Index("idx_alert_severity", "severity"),
    )


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    requested_service = Column(String(255), nullable=False)
    preferred_therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=True)
    priority = Column(String(20), default="medium")
    preferred_days = Column(JSON, nullable=True)
    preferred_time = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(50), default="waiting")
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient")
    preferred_therapist = relationship("Therapist")

    __table_args__ = (
        Index("idx_waitlist_patient_id", "patient_id"),
        Index("idx_waitlist_status", "status"),
        Index("idx_waitlist_priority", "priority"),
    )


# ============== DOCUMENTS ==============

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    document_type = Column(Enum(DocumentTypeEnum), nullable=False)
    title = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False)
    mime_type = Column(String(100), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    description = Column(Text, nullable=True)
    is_confidential = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("Patient", back_populates="documents")

    __table_args__ = (
        Index("idx_document_patient_id", "patient_id"),
        Index("idx_document_type", "document_type"),
    )


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
