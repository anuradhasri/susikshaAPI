"""Effective-dated therapist payout calculation and therapy-sheet tracking APIs."""
from calendar import monthrange
from datetime import date, datetime, time, timedelta
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import Notification, Patient, PatientPackage, PatientSlotBooking, Program, Role, Therapist, TherapistSlotMapping, User, UserRole
from app.models.payouts import (
    ProgramPaymentConfig, ProgramPaymentConfigHistory, TherapySheetAssignment,
    ProgramSheetConfig, TherapySheetSubmission, TherapistMonthlyPayout, TherapistPayoutAdjustment,
    TherapistSessionAllocation, TherapistSessionPayout, TherapistPayrollConfig,
    PatientProgramPricing,
)
from app.models.report_sheets import (
    PatientGoalSheet, PatientGoalSheetTherapist, PatientGoalSummary,
    PatientGoalSummaryTherapist, PatientObservationSheet,
    PatientObservationSheetTherapist,
)

router = APIRouter(prefix="/api/v1/payouts", tags=["therapist payouts"])
METHODS = {"percentage", "fixed", "package", "completion"}
SHEET_TYPES = {"observation", "goal", "goal_summary"}


def get_payout_manager(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    """Allow Admin and Front Office users to manage payout configuration and processing."""
    role_names = {
        str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
        for (name,) in (
            db.query(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .filter(
                UserRole.user_id == user.id,
                UserRole.deleted_at.is_(None),
                Role.deleted_at.is_(None),
            )
            .all()
        )
    }
    if not role_names.intersection({"admin", "front_office", "frontoffice", "front_officer"}):
        raise HTTPException(status_code=403, detail="Only Front Office or Admin users can manage therapist payouts")
    return user


def money(value):
    return round(float(value or 0), 2)


def config_shape(row):
    return {
        "id": row.id, "therapistId": row.therapist_id, "therapistName": row.therapist.name if getattr(row, "therapist", None) else None,
        "programId": row.program_id, "programName": row.program.program_name if getattr(row, "program", None) else None,
        "paymentMethod": row.payment_method, "percentage": row.percentage, "fixedAmount": row.fixed_amount,
        "gstRate": row.gst_rate, "therapistPoolPercentage": row.therapist_pool_percentage,
        "allocationRule": row.allocation_rule, "releaseOnCompletion": row.release_on_completion,
        "effectiveFrom": row.effective_from.isoformat(), "effectiveTo": row.effective_to.isoformat() if row.effective_to else None,
        "isActive": row.is_active,
    }


def snapshot(row):
    data = config_shape(row)
    data.pop("programName", None)
    return data


def applicable_config(db, therapist_id, program_id, session_date):
    return db.query(ProgramPaymentConfig).filter(
        ProgramPaymentConfig.therapist_id == therapist_id,
        ProgramPaymentConfig.program_id == program_id,
        ProgramPaymentConfig.is_active.is_(True),
        ProgramPaymentConfig.deleted_at.is_(None),
        ProgramPaymentConfig.effective_from <= session_date,
        or_(ProgramPaymentConfig.effective_to.is_(None), ProgramPaymentConfig.effective_to >= session_date),
    ).order_by(ProgramPaymentConfig.effective_from.desc()).first()


def applicable_sheet_configs(db, program_id, session_date):
    return db.query(ProgramSheetConfig).filter(
        ProgramSheetConfig.program_id == program_id,
        ProgramSheetConfig.is_active.is_(True),
        ProgramSheetConfig.is_required.is_(True),
        ProgramSheetConfig.deleted_at.is_(None),
        ProgramSheetConfig.effective_from <= session_date,
        or_(ProgramSheetConfig.effective_to.is_(None), ProgramSheetConfig.effective_to >= session_date),
    ).order_by(ProgramSheetConfig.sheet_type, ProgramSheetConfig.effective_from.desc()).all()


def applicable_payroll_config(db, therapist_id, on_date):
    return db.query(TherapistPayrollConfig).filter(
        TherapistPayrollConfig.therapist_id == therapist_id, TherapistPayrollConfig.is_active.is_(True),
        TherapistPayrollConfig.deleted_at.is_(None), TherapistPayrollConfig.effective_from <= on_date,
        or_(TherapistPayrollConfig.effective_to.is_(None), TherapistPayrollConfig.effective_to >= on_date),
    ).order_by(TherapistPayrollConfig.effective_from.desc()).first()


def applicable_patient_pricing(db, patient_id, program_id, on_date):
    return db.query(PatientProgramPricing).filter(
        PatientProgramPricing.patient_id == patient_id, PatientProgramPricing.program_id == program_id,
        PatientProgramPricing.is_active.is_(True), PatientProgramPricing.deleted_at.is_(None),
        PatientProgramPricing.effective_from <= on_date,
        or_(PatientProgramPricing.effective_to.is_(None), PatientProgramPricing.effective_to >= on_date),
    ).order_by(PatientProgramPricing.effective_from.desc()).first()


@router.get("/patient-pricing/{patient_id}")
def patient_pricing(patient_id: int, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(404, "Child not found")
    if getattr(user, "region_ids", None) and patient.region_id not in user.region_ids:
        raise HTTPException(403, "Child is outside your region access")
    rows = db.query(PatientProgramPricing).options(joinedload(PatientProgramPricing.program)).filter(
        PatientProgramPricing.patient_id == patient_id, PatientProgramPricing.deleted_at.is_(None)
    ).order_by(PatientProgramPricing.effective_from.desc(), PatientProgramPricing.id.desc()).all()
    return {"data": [{"id": row.id, "patientId": row.patient_id, "programId": row.program_id, "programName": row.program.program_name if row.program else f"Program {row.program_id}", "billingType": row.billing_type, "agreedAmount": money(row.agreed_amount), "totalSessions": row.total_sessions, "perSessionAmount": money(row.agreed_amount / row.total_sessions) if row.billing_type == "package" and row.total_sessions else money(row.agreed_amount), "effectiveFrom": row.effective_from.isoformat(), "effectiveTo": row.effective_to.isoformat() if row.effective_to else None, "isActive": row.is_active} for row in rows]}


@router.post("/patient-pricing/{patient_id}")
def save_patient_pricing(patient_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    patient = db.get(Patient, patient_id)
    program = db.get(Program, int(payload.get("programId") or 0))
    billing_type = str(payload.get("billingType") or "").lower()
    try:
        amount = money(payload.get("agreedAmount")); effective = datetime.strptime(str(payload.get("effectiveFrom") or ""), "%Y-%m-%d").date()
        total_sessions = int(payload.get("totalSessions") or 0) if billing_type == "package" else None
    except (TypeError, ValueError):
        raise HTTPException(400, "Enter valid pricing details")
    if not patient or not program or program.region_id != patient.region_id:
        raise HTTPException(400, "Select a valid program for this child’s centre")
    if getattr(user, "region_ids", None) and patient.region_id not in user.region_ids:
        raise HTTPException(403, "Child is outside your region access")
    if billing_type not in {"per_session", "package"} or amount <= 0 or (billing_type == "package" and total_sessions <= 0):
        raise HTTPException(400, "Amount and package sessions must be greater than zero")
    if db.query(PatientProgramPricing).filter_by(patient_id=patient_id, program_id=program.id, effective_from=effective).filter(PatientProgramPricing.deleted_at.is_(None)).first():
        raise HTTPException(409, "Pricing already exists for this program and effective date")
    previous = db.query(PatientProgramPricing).filter(PatientProgramPricing.patient_id == patient_id, PatientProgramPricing.program_id == program.id, PatientProgramPricing.effective_from < effective, PatientProgramPricing.deleted_at.is_(None)).order_by(PatientProgramPricing.effective_from.desc()).first()
    if previous and (previous.effective_to is None or previous.effective_to >= effective):
        previous.effective_to = effective - timedelta(days=1)
    row = PatientProgramPricing(patient_id=patient_id, program_id=program.id, billing_type=billing_type, agreed_amount=amount, total_sessions=total_sessions, effective_from=effective, created_by=user.id, updated_by=user.id)
    db.add(row); db.commit(); db.refresh(row)
    return {"data": {"id": row.id}}


@router.get("/notifications")
def notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        notification_rows = db.query(Notification).filter(Notification.user_id == user.id, Notification.deleted_at.is_(None)).order_by(Notification.created_at.desc()).limit(50).all()
    except ProgrammingError:
        # Older installations may not have run the notification migration yet.
        # Keep actionable, generated notifications available instead of failing
        # the header and every page that contains it.
        db.rollback()
        notification_rows = []
    items = [{
        "id": f"notification-{row.id}", "source": "notification", "sourceId": row.id,
        "title": row.title, "message": row.message, "type": row.notification_type,
        "isRead": bool(row.is_read), "createdAt": row.created_at.isoformat(),
        "url": (row.data or {}).get("url") if isinstance(row.data, dict) else None,
    } for row in notification_rows]
    therapist = db.query(Therapist).filter(Therapist.user_id == user.id).first()
    if therapist:
        assignments = db.query(TherapySheetAssignment).filter(TherapySheetAssignment.therapist_id == therapist.id, TherapySheetAssignment.status == "pending", TherapySheetAssignment.deleted_at.is_(None)).order_by(TherapySheetAssignment.assigned_at.desc()).all()
        for assignment in assignments:
            booking = db.get(PatientSlotBooking, assignment.patient_slot_booking_id)
            patient = booking.patient if booking else None
            program = db.get(Program, assignment.program_id) if assignment.program_id else None
            items.append({
                "id": f"sheet-{assignment.id}", "source": "therapy_sheet", "sourceId": assignment.id,
                "title": "Therapy sheet pending", "message": f"{patient.first_name if patient else 'Child'} · {(assignment.sheet_type or '').replace('_', ' ').title()}",
                "type": "warning", "isRead": assignment.notification_read_at is not None, "createdAt": assignment.assigned_at.isoformat(),
                "url": None, "patientId": assignment.patient_id, "sheetType": assignment.sheet_type,
                "therapistId": assignment.therapist_id,
                "sessionDate": booking.therapist_slot_mapping.slot_date.isoformat() if booking and booking.therapist_slot_mapping else None,
                "programName": program.program_name if program else None,
            })
    items.sort(key=lambda item: item["createdAt"], reverse=True)
    return {"data": {"items": items[:50], "unreadCount": sum(not item["isRead"] for item in items)}}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        row = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user.id, Notification.deleted_at.is_(None)).first()
    except ProgrammingError:
        db.rollback()
        raise HTTPException(404, "Notification storage is not available")
    if not row:
        raise HTTPException(404, "Notification was not found")
    row.is_read = True
    db.commit()
    return {"data": {"id": row.id, "isRead": True}}


@router.post("/notifications/sheets/{assignment_id}/read")
def mark_sheet_notification_read(assignment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    therapist = db.query(Therapist).filter(Therapist.user_id == user.id).first()
    row = db.query(TherapySheetAssignment).filter(TherapySheetAssignment.id == assignment_id, TherapySheetAssignment.deleted_at.is_(None)).first()
    if not row or (therapist and row.therapist_id != therapist.id):
        raise HTTPException(404, "Sheet notification was not found")
    row.notification_read_at = datetime.now()
    db.commit()
    return {"data": {"id": row.id, "isRead": True}}


@router.post("/notifications/read-all")
def mark_all_notifications_read(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read.is_(False), Notification.deleted_at.is_(None)).update({Notification.is_read: True}, synchronize_session=False)
    therapist = db.query(Therapist).filter(Therapist.user_id == user.id).first()
    if therapist:
        db.query(TherapySheetAssignment).filter(TherapySheetAssignment.therapist_id == therapist.id, TherapySheetAssignment.status == "pending", TherapySheetAssignment.notification_read_at.is_(None), TherapySheetAssignment.deleted_at.is_(None)).update({TherapySheetAssignment.notification_read_at: datetime.now()}, synchronize_session=False)
    db.commit()
    return {"data": {"success": True}}


@router.get("/dashboard")
def dashboard(month: str | None = None, region_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        month_start = datetime.strptime(month, "%Y-%m").date().replace(day=1) if month else date.today().replace(day=1)
    except ValueError:
        raise HTTPException(400, "month must use YYYY-MM")
    month_end = month_start.replace(day=monthrange(month_start.year, month_start.month)[1])
    scoped_therapist = db.query(Therapist).filter(Therapist.user_id == user.id).first()
    programs = db.query(Program).filter(Program.deleted_at.is_(None), Program.is_active.is_(True))
    if region_id:
        programs = programs.filter(Program.region_id == region_id)
    program_rows = programs.order_by(Program.program_name).all()
    program_ids = [p.id for p in program_rows]
    configs = db.query(ProgramPaymentConfig).options(joinedload(ProgramPaymentConfig.program), joinedload(ProgramPaymentConfig.therapist)).filter(
        ProgramPaymentConfig.program_id.in_(program_ids) if program_ids else False,
        ProgramPaymentConfig.deleted_at.is_(None),
    ).order_by(ProgramPaymentConfig.therapist_id, ProgramPaymentConfig.program_id, ProgramPaymentConfig.effective_from.desc()).all()
    payroll_configs_q = db.query(TherapistPayrollConfig).options(joinedload(TherapistPayrollConfig.therapist)).filter(
        TherapistPayrollConfig.deleted_at.is_(None)
    )
    sheets_q = db.query(TherapySheetAssignment).filter(TherapySheetAssignment.deleted_at.is_(None))
    payouts_q = db.query(TherapistSessionPayout).filter(TherapistSessionPayout.session_date.between(month_start, month_end), TherapistSessionPayout.deleted_at.is_(None))
    monthly_q = db.query(TherapistMonthlyPayout).filter(TherapistMonthlyPayout.payout_month == month_start, TherapistMonthlyPayout.deleted_at.is_(None))
    if scoped_therapist:
        sheets_q = sheets_q.filter(TherapySheetAssignment.therapist_id == scoped_therapist.id)
        payouts_q = payouts_q.filter(TherapistSessionPayout.therapist_id == scoped_therapist.id)
        monthly_q = monthly_q.filter(TherapistMonthlyPayout.therapist_id == scoped_therapist.id)
        configs = []
        payroll_configs_q = payroll_configs_q.filter(TherapistPayrollConfig.therapist_id == scoped_therapist.id)
    if region_id:
        therapist_ids = [r[0] for r in db.query(Therapist.id).filter(Therapist.region_id == region_id).all()]
        sheets_q = sheets_q.filter(TherapySheetAssignment.therapist_id.in_(therapist_ids) if therapist_ids else False)
        payouts_q = payouts_q.filter(TherapistSessionPayout.therapist_id.in_(therapist_ids) if therapist_ids else False)
        monthly_q = monthly_q.filter(TherapistMonthlyPayout.therapist_id.in_(therapist_ids) if therapist_ids else False)
        payroll_configs_q = payroll_configs_q.filter(TherapistPayrollConfig.therapist_id.in_(therapist_ids) if therapist_ids else False)
    sheets = sheets_q.order_by(TherapySheetAssignment.assigned_at.desc()).limit(200).all()
    payouts = payouts_q.order_by(TherapistSessionPayout.session_date.desc()).all()
    monthly = monthly_q.order_by(TherapistMonthlyPayout.therapist_id).all()
    payroll_configs = payroll_configs_q.order_by(TherapistPayrollConfig.therapist_id, TherapistPayrollConfig.effective_from.desc()).all()
    sheet_configs = db.query(ProgramSheetConfig).options(joinedload(ProgramSheetConfig.program)).filter(
        ProgramSheetConfig.program_id.in_(program_ids) if program_ids else False,
        ProgramSheetConfig.deleted_at.is_(None),
    ).order_by(ProgramSheetConfig.program_id, ProgramSheetConfig.sheet_type, ProgramSheetConfig.effective_from.desc()).all()
    therapist_ids = {x.therapist_id for x in sheets + payouts + monthly}
    names = dict(db.query(Therapist.id, Therapist.name).filter(Therapist.id.in_(therapist_ids)).all()) if therapist_ids else {}
    program_names = {p.id: p.program_name for p in program_rows}
    patient_names = {}
    session_dates = {}
    for assignment in sheets:
        booking = db.query(PatientSlotBooking).options(joinedload(PatientSlotBooking.patient)).get(assignment.patient_slot_booking_id)
        if booking and booking.patient:
            patient_names[assignment.patient_id] = f"{booking.patient.first_name} {booking.patient.last_name}".strip()
        if booking and booking.therapist_slot_mapping:
            session_dates[assignment.id] = booking.therapist_slot_mapping.slot_date.isoformat()
    return {"data": {
        "month": month_start.strftime("%Y-%m"),
        "programs": [{"id": p.id, "name": p.program_name, "regionId": p.region_id} for p in program_rows],
        "therapists": [{"id": t.id, "name": t.name, "regionId": t.region_id} for t in db.query(Therapist).filter(Therapist.is_active.is_(True), Therapist.region_id == region_id if region_id else True).order_by(Therapist.name).all()],
        "configs": [config_shape(c) for c in configs],
        "payrollConfigs": [{"id": c.id, "therapistId": c.therapist_id, "baseSalary": money(c.base_salary), "flatCommission": money(c.flat_commission), "professionalTax": money(c.professional_tax), "effectiveFrom": c.effective_from.isoformat(), "effectiveTo": c.effective_to.isoformat() if c.effective_to else None, "isActive": c.is_active} for c in payroll_configs],
        "sheetConfigs": [{"id": c.id, "programId": c.program_id, "programName": c.program.program_name if c.program else None, "sheetType": c.sheet_type, "isRequired": c.is_required, "effectiveFrom": c.effective_from.isoformat(), "effectiveTo": c.effective_to.isoformat() if c.effective_to else None, "isActive": c.is_active} for c in sheet_configs],
        "sheets": [{"id": s.id, "sessionId": s.patient_slot_booking_id, "patientId": s.patient_id, "therapistId": s.therapist_id, "sessionDate": session_dates.get(s.id), "patientName": patient_names.get(s.patient_id, f"Child {s.patient_id}"), "therapistName": names.get(s.therapist_id, f"Therapist {s.therapist_id}"), "programName": program_names.get(s.program_id, "—"), "sheetType": s.sheet_type, "status": s.status, "assignedAt": s.assigned_at.isoformat(), "dueAt": s.due_at.isoformat() if s.due_at else None, "notifiedAt": s.notified_at.isoformat() if s.notified_at else None} for s in sheets],
        "sessionPayouts": [{"id": p.id, "sessionId": p.patient_slot_booking_id, "date": p.session_date.isoformat(), "therapistName": names.get(p.therapist_id, f"Therapist {p.therapist_id}"), "programName": program_names.get(p.program_id, "—"), "eligibleAmount": money(p.eligible_amount), "gstAmount": money(p.gst_amount), "amountAfterGst": money(p.amount_after_gst), "therapistAmount": money(p.therapist_amount), "organizationAmount": money(p.organization_amount), "status": p.eligibility_status, "monthlyPayoutId": p.monthly_payout_id} for p in payouts],
        "monthlyPayouts": [{"id": p.id, "therapistName": names.get(p.therapist_id, f"Therapist {p.therapist_id}"), "grossAmount": money(p.gross_amount), "adjustmentAmount": money(p.adjustment_amount), "payableAmount": money(p.payable_amount), "status": p.status, "approvedAt": p.approved_at.isoformat() if p.approved_at else None, "paidAt": p.paid_at.isoformat() if p.paid_at else None, "paymentReference": p.payment_reference} for p in monthly],
        "summary": {"pendingSheets": sum(s.status == "pending" for s in sheets), "eligibleEarnings": money(sum(p.therapist_amount for p in payouts if p.eligibility_status == "eligible")), "pendingCompletion": sum(p.eligibility_status == "pending_completion" for p in payouts), "payable": money(sum(p.payable_amount for p in monthly))},
    }}


@router.post("/sheets/{assignment_id}/submit")
def submit_assigned_sheet(assignment_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assignment = db.get(TherapySheetAssignment, assignment_id)
    if not assignment or assignment.deleted_at is not None:
        raise HTTPException(404, "Sheet assignment was not found")
    therapist = db.query(Therapist).filter(Therapist.user_id == user.id).first()
    if therapist and assignment.therapist_id != therapist.id:
        raise HTTPException(403, "This sheet is assigned to another therapist")
    if not therapist:
        role_names = {str(name or "").strip().lower().replace("-", "_").replace(" ", "_") for (name,) in db.query(Role.name).join(UserRole, UserRole.role_id == Role.id).filter(UserRole.user_id == user.id, UserRole.deleted_at.is_(None), Role.deleted_at.is_(None)).all()}
        if not role_names.intersection({"admin", "front_office", "frontoffice", "front_officer"}):
            raise HTTPException(403, "Only the assigned therapist or Front Office can submit this sheet")
    source_id = int(payload.get("sourceId") or 0)
    source_models = {
        "observation": (PatientObservationSheet, "patient_observation_sheet"),
        "goal": (PatientGoalSheet, "patient_goal_sheet"),
        "goal_summary": (PatientGoalSummary, "patient_goal_summary"),
    }
    model, source_table = source_models.get(assignment.sheet_type, (None, None))
    source = db.get(model, source_id) if model and source_id else None
    if not source or source.patient_id != assignment.patient_id:
        raise HTTPException(400, "The saved sheet does not match this child and assignment")
    therapist_matches = False
    if assignment.sheet_type == "observation":
        therapist_matches = db.query(PatientObservationSheetTherapist).filter_by(sheet_id=source_id, therapist_id=assignment.therapist_id).first() is not None
    elif assignment.sheet_type == "goal":
        therapist_matches = source.therapist_id == assignment.therapist_id or db.query(PatientGoalSheetTherapist).filter_by(sheet_id=source_id, therapist_id=assignment.therapist_id).first() is not None
    elif assignment.sheet_type == "goal_summary":
        therapist_matches = source.therapist_id == assignment.therapist_id or db.query(PatientGoalSummaryTherapist).filter_by(summary_id=source_id, therapist_id=assignment.therapist_id).first() is not None
    if not therapist_matches:
        raise HTTPException(400, "Include the assigned therapist before submitting this sheet")
    submission = db.query(TherapySheetSubmission).filter(TherapySheetSubmission.assignment_id == assignment.id).first()
    if not submission:
        submission = TherapySheetSubmission(assignment_id=assignment.id, submitted_by=assignment.therapist_id)
        db.add(submission)
    submission.submitted_at = datetime.now()
    submission.source_table = source_table
    submission.source_id = source_id
    assignment.status = "submitted"
    db.commit()
    return {"data": {"assignmentId": assignment.id, "status": assignment.status}}


@router.post("/sheet-configs")
def save_sheet_config(payload: dict, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    program_id = int(payload.get("programId") or 0)
    sheet_type = str(payload.get("sheetType") or "").strip().lower()
    try:
        effective = datetime.strptime(payload.get("effectiveFrom", ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HTTPException(400, "A valid effective date is required")
    program = db.get(Program, program_id)
    if not program or sheet_type not in SHEET_TYPES:
        raise HTTPException(400, "Select a valid program and sheet type")
    if getattr(user, "region_ids", None) and program.region_id not in user.region_ids:
        raise HTTPException(403, "Program is outside your region access")
    existing = db.query(ProgramSheetConfig).filter_by(program_id=program_id, sheet_type=sheet_type, effective_from=effective).filter(ProgramSheetConfig.deleted_at.is_(None)).first()
    if existing:
        row = existing
    else:
        previous = db.query(ProgramSheetConfig).filter(ProgramSheetConfig.program_id == program_id, ProgramSheetConfig.sheet_type == sheet_type, ProgramSheetConfig.effective_from < effective, ProgramSheetConfig.deleted_at.is_(None)).order_by(ProgramSheetConfig.effective_from.desc()).first()
        if previous and (previous.effective_to is None or previous.effective_to >= effective):
            previous.effective_to = effective - timedelta(days=1)
        row = ProgramSheetConfig(program_id=program_id, sheet_type=sheet_type, effective_from=effective, created_by=user.id)
        db.add(row)
    row.is_required = bool(payload.get("isRequired", True))
    row.is_active = True
    row.updated_by = user.id
    db.commit(); db.refresh(row)
    return {"data": {"id": row.id, "programId": row.program_id, "sheetType": row.sheet_type, "effectiveFrom": row.effective_from.isoformat()}}


@router.post("/configs")
def save_config(payload: dict, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    therapist_id = int(payload.get("therapistId") or 0)
    program_id = int(payload.get("programId") or 0)
    method = str(payload.get("paymentMethod") or "").lower()
    try:
        effective = datetime.strptime(payload.get("effectiveFrom", ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HTTPException(400, "A valid effective date is required")
    program = db.get(Program, program_id)
    therapist = db.get(Therapist, therapist_id)
    if method not in METHODS or not program or not therapist:
        raise HTTPException(400, "Select a valid therapist, program, and payment method")
    if program.region_id != therapist.region_id:
        raise HTTPException(400, "Therapist and program must belong to the same centre")
    if getattr(user, "region_ids", None) and program.region_id not in user.region_ids:
        raise HTTPException(403, "Therapist and program are outside your region access")
    percentage = payload.get("percentage")
    fixed = payload.get("fixedAmount")
    gst_rate = money(payload.get("gstRate", 18))
    if not 0 <= gst_rate <= 100:
        raise HTTPException(400, "GST percentage must be between 0 and 100")
    if percentage not in (None, "") and not 0 <= float(percentage) <= 100:
        raise HTTPException(400, "Therapist percentage must be between 0 and 100")
    if method == "percentage" and percentage is None:
        raise HTTPException(400, "Percentage is required")
    if method in {"fixed", "completion"} and fixed is None and percentage is None:
        raise HTTPException(400, "Fixed amount or percentage is required")
    existing = db.query(ProgramPaymentConfig).filter(ProgramPaymentConfig.therapist_id == therapist_id, ProgramPaymentConfig.program_id == program_id, ProgramPaymentConfig.effective_from == effective, ProgramPaymentConfig.deleted_at.is_(None)).first()
    if existing:
        raise HTTPException(409, "This therapist payment rule is already saved and is view-only. Create a new rule with a different effective date.")
    else:
        previous = db.query(ProgramPaymentConfig).filter(ProgramPaymentConfig.therapist_id == therapist_id, ProgramPaymentConfig.program_id == program_id, ProgramPaymentConfig.effective_from < effective, ProgramPaymentConfig.deleted_at.is_(None)).order_by(ProgramPaymentConfig.effective_from.desc()).first()
        if previous and (previous.effective_to is None or previous.effective_to >= effective):
            previous.effective_to = effective - timedelta(days=1)
            db.add(ProgramPaymentConfigHistory(config_id=previous.id, action="closed", snapshot_json=json.dumps(snapshot(previous)), changed_by=user.id))
        row = ProgramPaymentConfig(therapist_id=therapist_id, program_id=program_id, effective_from=effective, created_by=user.id)
        db.add(row)
    row.payment_method = method
    row.percentage = float(percentage) if percentage not in (None, "") else None
    row.fixed_amount = float(fixed) if fixed not in (None, "") else None
    # Defaults to 18%, while allowing program-specific GST configuration.
    row.gst_rate = gst_rate
    program_name = str(program.program_name or "").lower()
    is_structured_program = "crt" in program_name or "structured" in program_name
    is_parent_program = "parent" in program_name
    is_vocational_program = "vocational" in program_name
    pool_value = float(payload.get("therapistPoolPercentage", 100)) if is_structured_program else float(payload.get("therapistPoolPercentage", 50)) if method == "package" else None
    if method == "package" and pool_value is not None and not 0 <= pool_value <= 100:
        raise HTTPException(400, "Therapist pool percentage must be between 0 and 100")
    row.therapist_pool_percentage = 40.0 if is_parent_program else 50.0 if is_vocational_program else pool_value
    row.allocation_rule = payload.get("allocationRule") or "equal_child_equivalent"
    row.release_on_completion = bool(payload.get("releaseOnCompletion") or method == "completion")
    row.is_active = True
    row.updated_by = user.id
    db.flush()
    db.add(ProgramPaymentConfigHistory(config_id=row.id, action="created" if not existing else "saved", snapshot_json=json.dumps(snapshot(row)), changed_by=user.id))
    db.commit()
    db.refresh(row)
    return {"data": config_shape(row)}


@router.post("/payroll-configs")
def save_payroll_config(payload: dict, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    therapist_id = int(payload.get("therapistId") or 0)
    try:
        effective = datetime.strptime(payload.get("effectiveFrom", ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HTTPException(400, "A valid effective date is required")
    therapist = db.get(Therapist, therapist_id)
    if not therapist:
        raise HTTPException(400, "Select a valid therapist")
    previous = db.query(TherapistPayrollConfig).filter(TherapistPayrollConfig.therapist_id == therapist_id, TherapistPayrollConfig.effective_from < effective, TherapistPayrollConfig.deleted_at.is_(None)).order_by(TherapistPayrollConfig.effective_from.desc()).first()
    if previous and (previous.effective_to is None or previous.effective_to >= effective):
        previous.effective_to = effective - timedelta(days=1)
    row = db.query(TherapistPayrollConfig).filter_by(therapist_id=therapist_id, effective_from=effective).first()
    if not row:
        row = TherapistPayrollConfig(therapist_id=therapist_id, effective_from=effective, created_by=user.id)
        db.add(row)
    row.base_salary = money(payload.get("baseSalary")); row.flat_commission = money(payload.get("flatCommission")); row.professional_tax = money(payload.get("professionalTax")); row.updated_by = user.id
    db.commit(); db.refresh(row)
    return {"data": {"id": row.id}}


@router.get("/billing-reports")
def billing_reports(month: str, region_id: int | None = None, therapist_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try: start = datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError: raise HTTPException(400, "month must use YYYY-MM")
    end = start.replace(day=monthrange(start.year, start.month)[1])
    scoped = db.query(Therapist).filter(Therapist.user_id == user.id).first()
    query = db.query(TherapistSessionPayout).filter(TherapistSessionPayout.session_date.between(start, end), TherapistSessionPayout.deleted_at.is_(None))
    if scoped: query = query.filter(TherapistSessionPayout.therapist_id == scoped.id)
    elif therapist_id: query = query.filter(TherapistSessionPayout.therapist_id == therapist_id)
    rows = query.order_by(TherapistSessionPayout.session_date).all()
    therapist_ids = {row.therapist_id for row in rows}
    therapists = {row.id: row for row in db.query(Therapist).filter(Therapist.id.in_(therapist_ids)).all()} if therapist_ids else {}
    if region_id:
        rows = [row for row in rows if row.therapist_id in therapists and therapists[row.therapist_id].region_id == region_id]
    program_ids = {row.program_id for row in rows}; booking_ids = {row.patient_slot_booking_id for row in rows}
    programs = {row.id: row.program_name for row in db.query(Program).filter(Program.id.in_(program_ids)).all()} if program_ids else {}
    bookings = {row.id: row for row in db.query(PatientSlotBooking).filter(PatientSlotBooking.id.in_(booking_ids)).all()} if booking_ids else {}
    patient_ids = {row.patient_id for row in bookings.values()}; patients = {row.id: f"{row.first_name} {row.last_name}".strip() for row in db.query(Patient).filter(Patient.id.in_(patient_ids)).all()} if patient_ids else {}
    detailed = []
    for row in rows:
        booking = bookings.get(row.patient_slot_booking_id); due = money(getattr(booking, "amount", 0)); received = money(getattr(booking, "paid_amount", 0) or getattr(booking, "package_covered_amount", 0)); balance = money(max(0, due - received))
        detailed.append({"id": row.id, "date": row.session_date.isoformat(), "therapistId": row.therapist_id, "therapist": therapists.get(row.therapist_id).name if therapists.get(row.therapist_id) else f"Therapist {row.therapist_id}", "child": patients.get(booking.patient_id, f"Child {booking.patient_id}") if booking else "—", "program": programs.get(row.program_id, "—"), "amountDue": due, "amountReceived": received, "balance": balance, "gst": money(row.gst_amount), "netAmount": money(row.amount_after_gst), "therapistPayout": money(row.therapist_amount)})
    def summarize(key):
        result = {}
        for item in detailed:
            group_key = (item[key], item["program"]); value = result.setdefault(group_key, {"name": item[key], "program": item["program"], "count": 0, "amountDue": 0, "amountReceived": 0, "balance": 0, "gst": 0, "netAmount": 0, "therapistPayout": 0})
            value["count"] += 1
            for field in ("amountDue", "amountReceived", "balance", "gst", "netAmount", "therapistPayout"): value[field] = money(value[field] + item[field])
        return list(result.values())
    payroll = []
    for tid in sorted({item["therapistId"] for item in detailed}):
        earnings = money(sum(item["therapistPayout"] for item in detailed if item["therapistId"] == tid)); config = applicable_payroll_config(db, tid, end)
        base = money(config.base_salary if config else 0); flat = money(config.flat_commission if config else 0); pt = money(config.professional_tax if config else 0)
        payroll.append({"therapistId": tid, "therapist": therapists[tid].name, "sessionEarnings": earnings, "baseSalary": base, "flatCommission": flat, "professionalTax": pt, "gross": money(base + flat + earnings), "netPay": money(base + flat + earnings - pt), "effectiveFrom": config.effective_from.isoformat() if config else None})
    totals = {field: money(sum(item[field] for item in detailed)) for field in ("amountDue", "amountReceived", "balance", "gst", "netAmount", "therapistPayout")}
    return {"data": {"month": month, "detailed": detailed, "children": summarize("child"), "therapists": summarize("therapist"), "payroll": payroll, "totals": totals}}


def completed(row):
    return row.status_id == 3 or str(getattr(row.patient_slot_booking_status_master, "code", "")).upper() == "COMPLETED"


@router.post("/sync")
def sync(start_date: date = Query(...), end_date: date = Query(...), region_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    if end_date < start_date:
        raise HTTPException(400, "end_date must be on or after start_date")
    query = db.query(PatientSlotBooking).options(
        joinedload(PatientSlotBooking.patient_slot_booking_status_master), joinedload(PatientSlotBooking.patient_package).joinedload(PatientPackage.package),
        joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.therapist), joinedload(PatientSlotBooking.program),
    ).join(TherapistSlotMapping).join(Therapist).filter(TherapistSlotMapping.slot_date.between(start_date, end_date))
    if region_id:
        query = query.filter(Therapist.region_id == region_id)
    rows = [r for r in query.all() if completed(r) and r.program_id]
    created_sheets = calculated = skipped = 0
    for row in rows:
        mapping = row.therapist_slot_mapping
        sheet_rules = applicable_sheet_configs(db, row.program_id, mapping.slot_date)
        for sheet_rule in sheet_rules:
            assignment = db.query(TherapySheetAssignment).filter_by(patient_slot_booking_id=row.id, therapist_id=mapping.therapist_id, sheet_type=sheet_rule.sheet_type).first()
            if not assignment:
                assignment = TherapySheetAssignment(patient_slot_booking_id=row.id, patient_id=row.patient_id, therapist_id=mapping.therapist_id, program_id=row.program_id, sheet_type=sheet_rule.sheet_type, due_at=datetime.combine(mapping.slot_date + timedelta(days=1), time(23, 59)), notified_at=datetime.utcnow())
                db.add(assignment); db.flush(); created_sheets += 1
            submitted = None
            source_table = None
            if sheet_rule.sheet_type == "observation":
                submitted = db.query(PatientObservationSheet).join(PatientObservationSheetTherapist).filter(PatientObservationSheet.patient_id == row.patient_id, PatientObservationSheet.observation_date == mapping.slot_date, PatientObservationSheetTherapist.therapist_id == mapping.therapist_id).first()
                source_table = "patient_observation_sheet"
            elif sheet_rule.sheet_type == "goal":
                submitted = db.query(PatientGoalSheet).join(PatientGoalSheetTherapist).filter(
                    PatientGoalSheet.patient_id == row.patient_id,
                    PatientGoalSheet.planning_month <= mapping.slot_date,
                    PatientGoalSheet.review_month >= mapping.slot_date,
                    PatientGoalSheetTherapist.therapist_id == mapping.therapist_id,
                ).order_by(PatientGoalSheet.planning_month.desc()).first()
                source_table = "patient_goal_sheet"
            elif sheet_rule.sheet_type == "goal_summary":
                submitted = db.query(PatientGoalSummary).outerjoin(PatientGoalSummaryTherapist).filter(
                    PatientGoalSummary.patient_id == row.patient_id,
                    PatientGoalSummary.evaluation_date == mapping.slot_date,
                    or_(PatientGoalSummary.therapist_id == mapping.therapist_id, PatientGoalSummaryTherapist.therapist_id == mapping.therapist_id),
                ).first()
                source_table = "patient_goal_summary"
            if submitted and assignment.status != "submitted":
                assignment.status = "submitted"
                if not db.query(TherapySheetSubmission).filter_by(assignment_id=assignment.id).first():
                    db.add(TherapySheetSubmission(assignment_id=assignment.id, submitted_by=mapping.therapist_id, source_table=source_table, source_id=submitted.id))
        config = applicable_config(db, mapping.therapist_id, row.program_id, mapping.slot_date)
        if not config or db.query(TherapistSessionPayout).filter_by(patient_slot_booking_id=row.id, therapist_id=mapping.therapist_id).first():
            skipped += 1; continue
        eligible = money(row.package_covered_amount or row.amount)
        package = row.patient_package.package if row.patient_package else None
        child_pricing = applicable_patient_pricing(db, row.patient_id, row.program_id, mapping.slot_date)
        if child_pricing:
            eligible = money(child_pricing.agreed_amount / child_pricing.total_sessions) if child_pricing.billing_type == "package" and child_pricing.total_sessions else money(child_pricing.agreed_amount)
        therapist_count = 1
        child_equivalent = 1.0
        if row.group_program_booking_id:
            group_rows = db.query(PatientSlotBooking).options(joinedload(PatientSlotBooking.therapist_slot_mapping)).filter(PatientSlotBooking.group_program_booking_id == row.group_program_booking_id).all()
            therapist_count = max(1, len({x.therapist_slot_mapping.therapist_id for x in group_rows if x.therapist_slot_mapping}))
            child_equivalent = len({x.patient_id for x in group_rows if x.patient_id}) / therapist_count
            eligible = money((row.amount or (package.price / package.total_sessions if package and package.total_sessions else 0)) * child_equivalent)
        gst_rate = float(config.gst_rate or 0)
        program_name = str(row.program.program_name if row.program else "").lower()
        is_structured_program = "crt" in program_name or "structured" in program_name
        is_parent_program = "parent" in program_name
        if is_structured_program:
            after_gst = money(eligible * (1 - gst_rate / 100))
            duration_minutes = int(row.duration_minutes or 0)
            structured_rates = {
                45: float(config.fixed_amount if config.fixed_amount is not None else 350),
                30: float(config.therapist_pool_percentage if config.therapist_pool_percentage is not None else 100),
            }
            therapist_amount = money(structured_rates.get(duration_minutes, 0))
        elif is_parent_program and package and package.total_sessions:
            after_gst = money(package.price * (1 - gst_rate / 100))
            therapist_amount = money(after_gst * 0.40 / package.total_sessions / therapist_count)
            eligible = money(package.price / package.total_sessions / therapist_count)
        elif is_parent_program:
            after_gst = money(eligible * (1 - gst_rate / 100))
            therapist_amount = money(after_gst * 0.40 / therapist_count)
        elif config.payment_method == "package" and package and package.total_sessions:
            after_gst = money(package.price * (1 - gst_rate / 100))
            therapist_amount = money(after_gst * float(config.therapist_pool_percentage or 50) / 100 / package.total_sessions / therapist_count)
            eligible = money(package.price / package.total_sessions / therapist_count)
        else:
            after_gst = money(eligible * (1 - gst_rate / 100))
            if config.fixed_amount is not None and config.payment_method in {"fixed", "completion"}:
                therapist_amount = money(config.fixed_amount)
            else:
                therapist_amount = money(after_gst * float(config.percentage or 0) / 100)
        gst = money(eligible - after_gst)
        status = "eligible"
        if config.release_on_completion:
            package_complete = bool(row.patient_package and (row.patient_package.sessions_remaining <= 0 or str(row.patient_package.status).lower() == "completed"))
            status = "eligible" if package_complete else "pending_completion"
        allocation = db.query(TherapistSessionAllocation).filter_by(patient_slot_booking_id=row.id, therapist_id=mapping.therapist_id).first()
        if not allocation:
            db.add(TherapistSessionAllocation(patient_slot_booking_id=row.id, therapist_id=mapping.therapist_id, child_equivalent=child_equivalent, allocation_percentage=100 / therapist_count))
        breakdown = {"method": "structured_duration_fixed" if is_structured_program else "parent_40_percent_pool" if is_parent_program else config.payment_method, "configId": config.id, "gstRate": gst_rate, "childEquivalent": child_equivalent, "therapistCount": therapist_count, "durationMinutes": int(row.duration_minutes or 0), "gstAlreadyDeducted": True}
        db.add(TherapistSessionPayout(patient_slot_booking_id=row.id, therapist_id=mapping.therapist_id, program_id=row.program_id, config_id=config.id, session_date=mapping.slot_date, eligible_amount=eligible, gst_amount=gst, amount_after_gst=after_gst, therapist_percentage=config.percentage, therapist_amount=therapist_amount, organization_amount=money(after_gst - therapist_amount), calculation_json=json.dumps(breakdown), eligibility_status=status))
        calculated += 1
    db.commit()
    return {"data": {"sheetAssignmentsCreated": created_sheets, "payoutsCalculated": calculated, "skipped": skipped}}


@router.post("/monthly/generate")
def generate_month(payload: dict, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    try: start = datetime.strptime(payload.get("month", ""), "%Y-%m").date().replace(day=1)
    except ValueError: raise HTTPException(400, "month must use YYYY-MM")
    end = start.replace(day=monthrange(start.year, start.month)[1])
    rows = db.query(TherapistSessionPayout).filter(TherapistSessionPayout.session_date.between(start, end), TherapistSessionPayout.eligibility_status == "eligible", TherapistSessionPayout.monthly_payout_id.is_(None), TherapistSessionPayout.deleted_at.is_(None)).all()
    grouped = {}
    for row in rows: grouped.setdefault(row.therapist_id, []).append(row)
    salaried_therapist_ids = {row[0] for row in db.query(TherapistPayrollConfig.therapist_id).filter(
        TherapistPayrollConfig.is_active.is_(True), TherapistPayrollConfig.deleted_at.is_(None),
        TherapistPayrollConfig.effective_from <= end,
        or_(TherapistPayrollConfig.effective_to.is_(None), TherapistPayrollConfig.effective_to >= end),
    ).distinct().all()}
    for therapist_id in salaried_therapist_ids:
        grouped.setdefault(therapist_id, [])
    generated = 0
    for therapist_id, earnings in grouped.items():
        monthly = db.query(TherapistMonthlyPayout).filter_by(therapist_id=therapist_id, payout_month=start).first()
        if not monthly:
            monthly = TherapistMonthlyPayout(therapist_id=therapist_id, payout_month=start, gross_amount=0, payable_amount=0)
            db.add(monthly); db.flush(); generated += 1
        if monthly.status != "draft": continue
        session_earnings = money(sum(x.therapist_amount for x in earnings))
        payroll_config = applicable_payroll_config(db, therapist_id, end)
        gross = money(session_earnings + (payroll_config.base_salary if payroll_config else 0) + (payroll_config.flat_commission if payroll_config else 0) - (payroll_config.professional_tax if payroll_config else 0))
        adjustment = money(sum(x.amount for x in db.query(TherapistPayoutAdjustment).filter_by(monthly_payout_id=monthly.id).all()))
        monthly.gross_amount = gross; monthly.adjustment_amount = adjustment; monthly.payable_amount = money(gross + adjustment)
        for earning in earnings: earning.monthly_payout_id = monthly.id
    db.commit()
    return {"data": {"generated": generated, "earningsIncluded": len(rows)}}


@router.post("/monthly/{payout_id}/status")
def update_status(payout_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    row = db.get(TherapistMonthlyPayout, payout_id)
    action = str(payload.get("status") or "").lower()
    if not row or action not in {"approved", "paid"}: raise HTTPException(400, "Invalid payout or status")
    row.status = action
    if action == "approved": row.approved_by = user.id; row.approved_at = datetime.utcnow()
    else:
        if not row.approved_at:
            row.approved_by = user.id; row.approved_at = datetime.utcnow()
        row.paid_at = datetime.utcnow(); row.payment_reference = payload.get("paymentReference")
    db.commit()
    return {"data": {"id": row.id, "status": row.status}}


@router.post("/monthly/{payout_id}/adjustments")
def add_adjustment(payout_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    row = db.get(TherapistMonthlyPayout, payout_id)
    if not row or row.deleted_at is not None:
        raise HTTPException(404, "Monthly payout not found")
    if row.status == "paid":
        raise HTTPException(409, "Paid payouts cannot be adjusted")
    try:
        amount = money(payload.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Enter a valid adjustment amount")
    reason = str(payload.get("reason") or "").strip()
    if amount == 0:
        raise HTTPException(400, "Adjustment amount cannot be zero")
    if not reason:
        raise HTTPException(400, "Adjustment reason is required")
    new_adjustment_total = money(row.adjustment_amount + amount)
    new_payable = money(row.gross_amount + new_adjustment_total)
    if new_payable < 0:
        raise HTTPException(400, "Adjustment cannot reduce the payable amount below zero")
    adjustment = TherapistPayoutAdjustment(monthly_payout_id=row.id, amount=amount, reason=reason, approved_by=user.id, approved_at=datetime.utcnow())
    db.add(adjustment)
    row.adjustment_amount = new_adjustment_total
    row.payable_amount = new_payable
    db.commit(); db.refresh(adjustment)
    return {"data": {"id": adjustment.id, "monthlyPayoutId": row.id, "amount": amount, "adjustmentAmount": row.adjustment_amount, "payableAmount": row.payable_amount}}
