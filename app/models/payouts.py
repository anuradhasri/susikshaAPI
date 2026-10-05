"""Therapy documentation and therapist payout ledger models."""
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.core.database import Base


class AuditColumns:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class PatientProgramPricing(AuditColumns, Base):
    __tablename__ = "patient_program_pricing"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False, index=True)
    billing_type = Column(String(24), nullable=False)
    agreed_amount = Column(Float, nullable=False)
    total_sessions = Column(Integer)
    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_by = Column(Integer, ForeignKey("users.id"))
    updated_by = Column(Integer, ForeignKey("users.id"))
    patient = relationship("Patient")
    program = relationship("Program")
    __table_args__ = (
        UniqueConstraint("patient_id", "program_id", "effective_from", name="uq_patient_program_pricing_effective"),
        Index("idx_patient_program_pricing_lookup", "patient_id", "program_id", "effective_from", "effective_to"),
    )


class ProgramPaymentConfig(AuditColumns, Base):
    __tablename__ = "program_payment_config"
    id = Column(Integer, primary_key=True)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False, index=True)
    payment_method = Column(String(32), nullable=False)  # percentage, fixed, package, completion
    percentage = Column(Float)
    fixed_amount = Column(Float)
    gst_rate = Column(Float, nullable=False, default=18, server_default="18")
    therapist_pool_percentage = Column(Float)
    allocation_rule = Column(String(32), nullable=False, default="equal_child_equivalent", server_default="equal_child_equivalent")
    release_on_completion = Column(Boolean, nullable=False, default=False, server_default="0")
    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_by = Column(Integer, ForeignKey("users.id"))
    updated_by = Column(Integer, ForeignKey("users.id"))
    program = relationship("Program")
    therapist = relationship("Therapist")
    __table_args__ = (
        UniqueConstraint("therapist_id", "program_id", "effective_from", name="uq_therapist_program_payment_effective_date"),
        Index("idx_program_payment_lookup", "therapist_id", "program_id", "effective_from", "effective_to"),
    )


class ProgramPaymentConfigHistory(Base):
    __tablename__ = "program_payment_config_history"
    id = Column(Integer, primary_key=True)
    config_id = Column(Integer, ForeignKey("program_payment_config.id"), nullable=False, index=True)
    action = Column(String(24), nullable=False)
    snapshot_json = Column(Text, nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"))
    changed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProgramSheetConfig(AuditColumns, Base):
    __tablename__ = "program_sheet_config"
    id = Column(Integer, primary_key=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False, index=True)
    sheet_type = Column(String(32), nullable=False)
    is_required = Column(Boolean, nullable=False, default=True, server_default="1")
    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_by = Column(Integer, ForeignKey("users.id"))
    updated_by = Column(Integer, ForeignKey("users.id"))
    program = relationship("Program")
    __table_args__ = (
        UniqueConstraint("program_id", "sheet_type", "effective_from", name="uq_program_sheet_effective_date"),
        Index("idx_program_sheet_lookup", "program_id", "effective_from", "effective_to"),
    )


class TherapySheetAssignment(AuditColumns, Base):
    __tablename__ = "therapy_sheet_assignment"
    id = Column(Integer, primary_key=True)
    patient_slot_booking_id = Column(Integer, ForeignKey("patient_slot_booking.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), index=True)
    sheet_type = Column(String(32), nullable=False, default="observation", server_default="observation")
    assigned_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    due_at = Column(DateTime(timezone=True))
    status = Column(String(24), nullable=False, default="pending", server_default="pending", index=True)
    notified_at = Column(DateTime(timezone=True))
    notification_read_at = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("patient_slot_booking_id", "therapist_id", "sheet_type", name="uq_session_therapist_sheet"),)


class TherapySheetSubmission(AuditColumns, Base):
    __tablename__ = "therapy_sheet_submission"
    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("therapy_sheet_assignment.id"), nullable=False, unique=True)
    submitted_by = Column(Integer, ForeignKey("therapists.id"), nullable=False)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    source_table = Column(String(64))
    source_id = Column(Integer)
    notes = Column(Text)


class TherapistSessionAllocation(AuditColumns, Base):
    __tablename__ = "therapist_session_allocation"
    id = Column(Integer, primary_key=True)
    patient_slot_booking_id = Column(Integer, ForeignKey("patient_slot_booking.id"), nullable=False)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False)
    child_equivalent = Column(Float, nullable=False, default=1, server_default="1")
    allocation_percentage = Column(Float, nullable=False, default=100, server_default="100")
    __table_args__ = (UniqueConstraint("patient_slot_booking_id", "therapist_id", name="uq_session_therapist_allocation"),)


class TherapistSessionPayout(AuditColumns, Base):
    __tablename__ = "therapist_session_payout"
    id = Column(Integer, primary_key=True)
    patient_slot_booking_id = Column(Integer, ForeignKey("patient_slot_booking.id"), nullable=False)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False, index=True)
    config_id = Column(Integer, ForeignKey("program_payment_config.id"), nullable=False)
    session_date = Column(Date, nullable=False, index=True)
    eligible_amount = Column(Float, nullable=False)
    gst_amount = Column(Float, nullable=False)
    amount_after_gst = Column(Float, nullable=False)
    therapist_percentage = Column(Float)
    therapist_amount = Column(Float, nullable=False)
    organization_amount = Column(Float, nullable=False)
    calculation_json = Column(Text, nullable=False)
    eligibility_status = Column(String(24), nullable=False, default="eligible", server_default="eligible")
    monthly_payout_id = Column(Integer, ForeignKey("therapist_monthly_payout.id"), index=True)
    __table_args__ = (UniqueConstraint("patient_slot_booking_id", "therapist_id", name="uq_session_therapist_payout"),)


class TherapistProgramPayout(AuditColumns, Base):
    __tablename__ = "therapist_program_payout"
    id = Column(Integer, primary_key=True)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    patient_package_id = Column(Integer, ForeignKey("patient_packages.id"), index=True)
    config_id = Column(Integer, ForeignKey("program_payment_config.id"), nullable=False)
    earned_date = Column(Date)
    eligible_amount = Column(Float, nullable=False, default=0, server_default="0")
    therapist_amount = Column(Float, nullable=False, default=0, server_default="0")
    status = Column(String(24), nullable=False, default="pending", server_default="pending")
    calculation_json = Column(Text, nullable=False)
    monthly_payout_id = Column(Integer, ForeignKey("therapist_monthly_payout.id"), index=True)


class TherapistMonthlyPayout(AuditColumns, Base):
    __tablename__ = "therapist_monthly_payout"
    id = Column(Integer, primary_key=True)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False, index=True)
    payout_month = Column(Date, nullable=False, index=True)
    gross_amount = Column(Float, nullable=False)
    adjustment_amount = Column(Float, nullable=False, default=0, server_default="0")
    payable_amount = Column(Float, nullable=False)
    status = Column(String(24), nullable=False, default="draft", server_default="draft")
    approved_by = Column(Integer, ForeignKey("users.id"))
    approved_at = Column(DateTime(timezone=True))
    paid_at = Column(DateTime(timezone=True))
    payment_reference = Column(String(128))
    __table_args__ = (UniqueConstraint("therapist_id", "payout_month", name="uq_therapist_monthly_payout"),)


class TherapistPayoutAdjustment(AuditColumns, Base):
    __tablename__ = "therapist_payout_adjustment"
    id = Column(Integer, primary_key=True)
    monthly_payout_id = Column(Integer, ForeignKey("therapist_monthly_payout.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    reason = Column(Text, nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"))
    approved_at = Column(DateTime(timezone=True))


class TherapistPayrollConfig(AuditColumns, Base):
    __tablename__ = "therapist_payroll_config"
    id = Column(Integer, primary_key=True)
    therapist_id = Column(Integer, ForeignKey("therapists.id"), nullable=False, index=True)
    base_salary = Column(Float, nullable=False, default=0, server_default="0")
    flat_commission = Column(Float, nullable=False, default=0, server_default="0")
    professional_tax = Column(Float, nullable=False, default=0, server_default="0")
    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_by = Column(Integer, ForeignKey("users.id"))
    updated_by = Column(Integer, ForeignKey("users.id"))
    therapist = relationship("Therapist")
    __table_args__ = (UniqueConstraint("therapist_id", "effective_from", name="uq_therapist_payroll_effective_date"),)
