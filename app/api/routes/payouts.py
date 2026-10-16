"""Effective-dated therapist payout calculation and therapy-sheet tracking APIs."""
from calendar import monthrange
from datetime import date, datetime, time, timedelta
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import CrtProgramBooking, Notification, Patient, PatientPackage, PatientSlotBooking, Payment, Program, Role, Therapist, TherapistSlotMapping, User, UserRole
from app.models.payouts import (
    ProgramPaymentConfig, ProgramPaymentConfigHistory, TherapySheetAssignment,
    ProgramSheetConfig, TherapySheetSubmission, TherapistMonthlyPayout, TherapistPayoutAdjustment,
    TherapistSessionAllocation, TherapistSessionPayout, TherapistPayrollConfig,
    PatientProgramPricing, TherapistPaymentEarning, PayrollTaxSetting,
)
from app.services.payment_earning_service import sync_payment_earnings
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


def inclusive_gst(amount, rate):
    """Extract GST already included in an amount (for example, 18 out of 118)."""
    gross = money(amount)
    gst_rate = float(rate or 0)
    if gross <= 0 or gst_rate <= 0:
        return 0.0, gross
    gst = money(gross * gst_rate / (100 + gst_rate))
    return gst, money(gross - gst)


def tax_settings(db):
    row = db.query(PayrollTaxSetting).filter(PayrollTaxSetting.deleted_at.is_(None)).order_by(PayrollTaxSetting.id).first()
    return row or PayrollTaxSetting(income_threshold=25000, regular_month_amount=200, march_amount=300)


def professional_tax_for(db, gross_income, payroll_month):
    settings = tax_settings(db)
    if money(gross_income) <= money(settings.income_threshold):
        return 0.0
    return money(settings.march_amount if payroll_month.month == 3 else settings.regular_month_amount)


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
    base_query = db.query(ProgramPaymentConfig).filter(
        ProgramPaymentConfig.therapist_id == therapist_id,
        ProgramPaymentConfig.program_id == program_id,
        ProgramPaymentConfig.is_active.is_(True),
        ProgramPaymentConfig.deleted_at.is_(None),
    )
    effective_config = base_query.filter(
        ProgramPaymentConfig.effective_from <= session_date,
        or_(ProgramPaymentConfig.effective_to.is_(None), ProgramPaymentConfig.effective_to >= session_date),
    ).order_by(ProgramPaymentConfig.effective_from.desc()).first()
    if effective_config:
        return effective_config
    # Historical sessions must remain reportable when payout setup was created
    # after the sessions. Use the earliest active rule only when no historical
    # rule existed for this therapist and program.
    return base_query.order_by(ProgramPaymentConfig.effective_from.asc()).first()


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


@router.get("/therapists/{therapist_id}/active-setup")
def active_therapist_payment_setup(therapist_id: int, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    therapist = db.get(Therapist, therapist_id)
    if not therapist:
        raise HTTPException(404, "Therapist not found")
    if getattr(user, "region_ids", None) and therapist.region_id not in user.region_ids:
        raise HTTPException(403, "Therapist is outside your region access")
    today = date.today()
    payroll = applicable_payroll_config(db, therapist_id, today)
    rules = db.query(ProgramPaymentConfig).options(joinedload(ProgramPaymentConfig.program)).filter(
        ProgramPaymentConfig.therapist_id == therapist_id,
        ProgramPaymentConfig.is_active.is_(True),
        ProgramPaymentConfig.deleted_at.is_(None),
        ProgramPaymentConfig.effective_from <= today,
        or_(ProgramPaymentConfig.effective_to.is_(None), ProgramPaymentConfig.effective_to >= today),
    ).order_by(ProgramPaymentConfig.program_id, ProgramPaymentConfig.effective_from.desc()).all()
    latest_by_program = {}
    for rule in rules:
        latest_by_program.setdefault(rule.program_id, rule)
    return {"data": {
        "therapistId": therapist.id,
        "therapistName": therapist.name,
        "payroll": None if not payroll else {
            "baseSalary": money(payroll.base_salary),
            "flatCommission": money(payroll.flat_commission),
            "professionalTax": money(payroll.professional_tax),
            "fixedSalaryOnly": bool(payroll.fixed_salary_only),
            "effectiveFrom": payroll.effective_from.isoformat(),
            "effectiveTo": payroll.effective_to.isoformat() if payroll.effective_to else None,
        },
        "rules": [config_shape(rule) for rule in latest_by_program.values()],
    }}


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
    parent_program_ids = [p.id for p in program_rows if "parent" in str(p.program_name or "").lower()]
    payouts_q = db.query(TherapistSessionPayout).join(PatientSlotBooking, PatientSlotBooking.id == TherapistSessionPayout.patient_slot_booking_id).filter(TherapistSessionPayout.session_date.between(month_start, month_end), TherapistSessionPayout.deleted_at.is_(None))
    if parent_program_ids:
        payouts_q = payouts_q.filter(~TherapistSessionPayout.program_id.in_(parent_program_ids))
    ledger_exists = db.query(TherapistPaymentEarning.id).filter(
        TherapistPaymentEarning.patient_slot_booking_id == TherapistSessionPayout.patient_slot_booking_id,
        TherapistPaymentEarning.therapist_id == TherapistSessionPayout.therapist_id,
        TherapistPaymentEarning.deleted_at.is_(None),
    ).exists()
    payouts_q = payouts_q.filter(~ledger_exists)
    earnings_q = db.query(TherapistPaymentEarning).filter(TherapistPaymentEarning.earning_date.between(month_start, month_end), TherapistPaymentEarning.deleted_at.is_(None))
    monthly_q = db.query(TherapistMonthlyPayout).filter(TherapistMonthlyPayout.payout_month == month_start, TherapistMonthlyPayout.deleted_at.is_(None))
    if scoped_therapist:
        sheets_q = sheets_q.filter(TherapySheetAssignment.therapist_id == scoped_therapist.id)
        payouts_q = payouts_q.filter(TherapistSessionPayout.therapist_id == scoped_therapist.id)
        earnings_q = earnings_q.filter(TherapistPaymentEarning.therapist_id == scoped_therapist.id)
        monthly_q = monthly_q.filter(TherapistMonthlyPayout.therapist_id == scoped_therapist.id)
        configs = []
        payroll_configs_q = payroll_configs_q.filter(TherapistPayrollConfig.therapist_id == scoped_therapist.id)
    if region_id:
        therapist_ids = [r[0] for r in db.query(Therapist.id).filter(Therapist.region_id == region_id).all()]
        sheets_q = sheets_q.filter(TherapySheetAssignment.therapist_id.in_(therapist_ids) if therapist_ids else False)
        payouts_q = payouts_q.filter(TherapistSessionPayout.therapist_id.in_(therapist_ids) if therapist_ids else False)
        earnings_q = earnings_q.filter(TherapistPaymentEarning.therapist_id.in_(therapist_ids) if therapist_ids else False)
        monthly_q = monthly_q.filter(TherapistMonthlyPayout.therapist_id.in_(therapist_ids) if therapist_ids else False)
        payroll_configs_q = payroll_configs_q.filter(TherapistPayrollConfig.therapist_id.in_(therapist_ids) if therapist_ids else False)
    sheets = sheets_q.order_by(TherapySheetAssignment.assigned_at.desc()).limit(200).all()
    payouts = payouts_q.order_by(TherapistSessionPayout.session_date.desc()).all()
    payment_earnings = earnings_q.order_by(TherapistPaymentEarning.earning_date.desc(), TherapistPaymentEarning.id.desc()).all()
    payout_booking_ids = [p.patient_slot_booking_id for p in payouts]
    payout_bookings = {booking.id: booking for booking in db.query(PatientSlotBooking).filter(PatientSlotBooking.id.in_(payout_booking_ids) if payout_booking_ids else False).all()}
    payout_groups = {}
    for payout in payouts:
        booking = payout_bookings.get(payout.patient_slot_booking_id)
        session_key = f"group:{booking.group_program_booking_id}" if booking and booking.group_program_booking_id else f"booking:{payout.patient_slot_booking_id}"
        key = (session_key, payout.therapist_id)
        item = payout_groups.setdefault(key, {"id": payout.id, "sessionId": payout.patient_slot_booking_id, "date": payout.session_date.isoformat(), "therapistId": payout.therapist_id, "programId": payout.program_id, "eligibleAmount": 0.0, "gstAmount": 0.0, "amountAfterGst": 0.0, "therapistAmount": 0.0, "organizationAmount": 0.0, "status": "pending_payment", "monthlyPayoutId": payout.monthly_payout_id})
        for field, value in (("eligibleAmount", payout.eligible_amount), ("gstAmount", payout.gst_amount), ("amountAfterGst", payout.amount_after_gst), ("therapistAmount", payout.therapist_amount), ("organizationAmount", payout.organization_amount)):
            item[field] = money(item[field] + float(value or 0))
        if payout.eligibility_status == "eligible" and float(payout.therapist_amount or 0) > 0:
            item["status"] = "eligible"
        elif payout.eligibility_status == "pending_completion" and item["status"] != "eligible":
            item["status"] = "pending_completion"
    for earning in payment_earnings:
        key = (f"payment:{earning.payment_id}", earning.therapist_id)
        payout_groups[key] = {"id": f"payment-{earning.id}", "sessionId": earning.patient_slot_booking_id, "date": earning.earning_date.isoformat(), "therapistId": earning.therapist_id, "programId": earning.program_id, "eligibleAmount": money(earning.received_amount), "gstAmount": money(earning.gst_amount), "amountAfterGst": money(earning.amount_after_gst), "therapistAmount": money(earning.therapist_amount), "organizationAmount": money(earning.organization_amount), "status": earning.status, "monthlyPayoutId": earning.monthly_payout_id}
    monthly = monthly_q.order_by(TherapistMonthlyPayout.therapist_id).all()
    payroll_configs = payroll_configs_q.order_by(TherapistPayrollConfig.therapist_id, TherapistPayrollConfig.effective_from.desc()).all()
    sheet_configs = db.query(ProgramSheetConfig).options(joinedload(ProgramSheetConfig.program)).filter(
        ProgramSheetConfig.program_id.in_(program_ids) if program_ids else False,
        ProgramSheetConfig.deleted_at.is_(None),
    ).order_by(ProgramSheetConfig.program_id, ProgramSheetConfig.sheet_type, ProgramSheetConfig.effective_from.desc()).all()
    therapist_ids = {x.therapist_id for x in sheets + payouts + monthly + payment_earnings}
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
    session_payout_rows = [
        {
            **{key: value for key, value in item.items() if key not in {"therapistId", "programId"}},
            "therapistName": names.get(item["therapistId"], f"Therapist {item['therapistId']}"),
            "programName": program_names.get(item["programId"], "—"),
        }
        for item in payout_groups.values()
    ]
    session_payout_rows.sort(key=lambda item: (item["status"] != "eligible", -date.fromisoformat(item["date"]).toordinal(), item["therapistName"]))
    pt_settings = tax_settings(db)
    return {"data": {
        "month": month_start.strftime("%Y-%m"),
        "programs": [{"id": p.id, "name": p.program_name, "regionId": p.region_id} for p in program_rows],
        "therapists": [{"id": t.id, "name": t.name, "regionId": t.region_id} for t in db.query(Therapist).filter(Therapist.is_active.is_(True), Therapist.region_id == region_id if region_id else True).order_by(Therapist.name).all()],
        "configs": [config_shape(c) for c in configs],
        "payrollConfigs": [{"id": c.id, "therapistId": c.therapist_id, "baseSalary": money(c.base_salary), "flatCommission": money(c.flat_commission), "professionalTax": money(c.professional_tax), "fixedSalaryOnly": bool(c.fixed_salary_only), "effectiveFrom": c.effective_from.isoformat(), "effectiveTo": c.effective_to.isoformat() if c.effective_to else None, "isActive": c.is_active} for c in payroll_configs],
        "taxSettings": {"incomeThreshold": money(pt_settings.income_threshold), "regularMonthAmount": money(pt_settings.regular_month_amount), "marchAmount": money(pt_settings.march_amount)},
        "sheetConfigs": [{"id": c.id, "programId": c.program_id, "programName": c.program.program_name if c.program else None, "sheetType": c.sheet_type, "isRequired": c.is_required, "effectiveFrom": c.effective_from.isoformat(), "effectiveTo": c.effective_to.isoformat() if c.effective_to else None, "isActive": c.is_active} for c in sheet_configs],
        "sheets": [{"id": s.id, "sessionId": s.patient_slot_booking_id, "patientId": s.patient_id, "therapistId": s.therapist_id, "sessionDate": session_dates.get(s.id), "patientName": patient_names.get(s.patient_id, f"Child {s.patient_id}"), "therapistName": names.get(s.therapist_id, f"Therapist {s.therapist_id}"), "programName": program_names.get(s.program_id, "—"), "sheetType": s.sheet_type, "status": s.status, "assignedAt": s.assigned_at.isoformat(), "dueAt": s.due_at.isoformat() if s.due_at else None, "notifiedAt": s.notified_at.isoformat() if s.notified_at else None} for s in sheets],
        "sessionPayouts": session_payout_rows,
        "monthlyPayouts": [{"id": p.id, "therapistName": names.get(p.therapist_id, f"Therapist {p.therapist_id}"), "grossAmount": money(p.gross_amount), "adjustmentAmount": money(p.adjustment_amount), "payableAmount": money(p.payable_amount), "status": p.status, "approvedAt": p.approved_at.isoformat() if p.approved_at else None, "paidAt": p.paid_at.isoformat() if p.paid_at else None, "paymentReference": p.payment_reference} for p in monthly],
        "summary": {"pendingSheets": sum(s.status == "pending" for s in sheets), "eligibleEarnings": money(sum(p.therapist_amount for p in payouts if p.eligibility_status == "eligible") + sum(e.therapist_amount for e in payment_earnings if e.status == "eligible")), "pendingCompletion": sum(p.eligibility_status == "pending_completion" for p in payouts), "payable": money(sum(p.payable_amount for p in monthly))},
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
    program_name = str(program.program_name or "").lower() if program else ""
    is_vocational_program = "vocational" in program_name
    is_structured_program = "crt" in program_name or "structured" in program_name
    if method not in METHODS or not program or not therapist:
        raise HTTPException(400, "Select a valid therapist, program, and payment method")
    if program.region_id != therapist.region_id:
        raise HTTPException(400, "Therapist and program must belong to the same centre")
    if getattr(user, "region_ids", None) and program.region_id not in user.region_ids:
        raise HTTPException(403, "Therapist and program are outside your region access")
    percentage = payload.get("percentage")
    fixed = payload.get("fixedAmount")
    if is_vocational_program:
        method, fixed, percentage = "fixed", 529, None
    gst_rate = 0 if is_vocational_program or is_structured_program else money(payload.get("gstRate", 18))
    if not 0 <= gst_rate <= 100:
        raise HTTPException(400, "GST percentage must be between 0 and 100")
    if percentage not in (None, "") and not 0 <= float(percentage) <= 100:
        raise HTTPException(400, "Therapist percentage must be between 0 and 100")
    if method == "percentage" and percentage is None:
        raise HTTPException(400, "Percentage is required")
    if method in {"fixed", "completion"} and fixed is None and percentage is None:
        raise HTTPException(400, "Fixed amount or percentage is required")
    existing = db.query(ProgramPaymentConfig).filter(ProgramPaymentConfig.therapist_id == therapist_id, ProgramPaymentConfig.program_id == program_id, ProgramPaymentConfig.effective_from == effective, ProgramPaymentConfig.deleted_at.is_(None)).first()
    previous_snapshot = snapshot(existing) if existing else None
    if existing:
        row = existing
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
    is_parent_program = "parent" in program_name
    pool_default = 100 if is_structured_program else 0 if is_parent_program else 50
    pool_value = float(payload.get("therapistPoolPercentage", pool_default)) if method == "package" or is_structured_program else None
    if method == "package" and pool_value is not None and not 0 <= pool_value <= 100:
        raise HTTPException(400, "Therapist pool percentage must be between 0 and 100")
    row.therapist_pool_percentage = pool_value
    row.allocation_rule = payload.get("allocationRule") or "equal_child_equivalent"
    row.release_on_completion = bool(payload.get("releaseOnCompletion") or method == "completion")
    row.is_active = True
    row.updated_by = user.id
    db.flush()
    if previous_snapshot:
        db.add(ProgramPaymentConfigHistory(config_id=row.id, action="updated", snapshot_json=json.dumps(previous_snapshot), changed_by=user.id))
    db.add(ProgramPaymentConfigHistory(config_id=row.id, action="created" if not existing else "updated_to", snapshot_json=json.dumps(snapshot(row)), changed_by=user.id))
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
    row.fixed_salary_only = bool(payload.get("fixedSalaryOnly", False)); row.base_salary = money(payload.get("baseSalary")) if row.fixed_salary_only else 0; row.flat_commission = 0; row.professional_tax = 0; row.updated_by = user.id
    db.commit(); db.refresh(row)
    return {"data": {"id": row.id}}


@router.post("/tax-settings")
def save_tax_settings(payload: dict, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    values = [money(payload.get(key)) for key in ("incomeThreshold", "regularMonthAmount", "marchAmount")]
    if any(value < 0 for value in values):
        raise HTTPException(400, "Professional tax values cannot be negative")
    row = db.query(PayrollTaxSetting).filter(PayrollTaxSetting.deleted_at.is_(None)).order_by(PayrollTaxSetting.id).first()
    if not row:
        row = PayrollTaxSetting()
        db.add(row)
    row.income_threshold, row.regular_month_amount, row.march_amount = values
    row.updated_by = user.id
    db.commit(); db.refresh(row)
    return {"data": {"incomeThreshold": money(row.income_threshold), "regularMonthAmount": money(row.regular_month_amount), "marchAmount": money(row.march_amount)}}


@router.get("/billing-reports")
def billing_reports(month: str, region_id: int | None = None, therapist_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try: start = datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError: raise HTTPException(400, "month must use YYYY-MM")
    end = start.replace(day=monthrange(start.year, start.month)[1])
    scoped = db.query(Therapist).filter(Therapist.user_id == user.id).first()
    query = db.query(TherapistSessionPayout).filter(TherapistSessionPayout.session_date.between(start, end), TherapistSessionPayout.deleted_at.is_(None))
    if scoped: query = query.filter(TherapistSessionPayout.therapist_id == scoped.id)
    elif therapist_id: query = query.filter(TherapistSessionPayout.therapist_id == therapist_id)
    rows = query.order_by(
        TherapistSessionPayout.session_date.desc(),
        TherapistSessionPayout.id.desc(),
    ).all()
    therapist_ids = {row.therapist_id for row in rows}
    therapists = {row.id: row for row in db.query(Therapist).filter(Therapist.id.in_(therapist_ids)).all()} if therapist_ids else {}
    if region_id:
        rows = [row for row in rows if row.therapist_id in therapists and therapists[row.therapist_id].region_id == region_id]
    program_ids = {row.program_id for row in rows}; booking_ids = {row.patient_slot_booking_id for row in rows}
    programs = {row.id: row.program_name for row in db.query(Program).filter(Program.id.in_(program_ids)).all()} if program_ids else {}
    bookings = {row.id: row for row in db.query(PatientSlotBooking).filter(PatientSlotBooking.id.in_(booking_ids)).all()} if booking_ids else {}
    ledger_rows = db.query(Payment).filter(Payment.patient_slot_booking_id.in_(booking_ids)).order_by(Payment.id).all() if booking_ids else []
    payments_by_booking = {}
    for payment in ledger_rows:
        payments_by_booking.setdefault(int(payment.patient_slot_booking_id), []).append(payment)
    crt_parent_ids = {booking.crt_program_booking_id for booking in bookings.values() if booking.crt_program_booking_id}
    crt_parents = {parent.id: parent for parent in db.query(CrtProgramBooking).filter(CrtProgramBooking.id.in_(crt_parent_ids)).all()} if crt_parent_ids else {}
    config_ids = {row.config_id for row in rows}
    payout_configs = {config.id: config for config in db.query(ProgramPaymentConfig).filter(ProgramPaymentConfig.id.in_(config_ids)).all()} if config_ids else {}
    patient_ids = {row.patient_id for row in bookings.values()} | {row.patient_id for row in ledger_rows if row.patient_id}; patients = {row.id: f"{row.first_name} {row.last_name}".strip() for row in db.query(Patient).filter(Patient.id.in_(patient_ids)).all()} if patient_ids else {}
    group_therapist_ids = {}
    group_child_bookings = {}
    for payout_row in rows:
        payout_booking = bookings.get(payout_row.patient_slot_booking_id)
        group_id = getattr(payout_booking, "group_program_booking_id", None) if payout_booking else None
        if not group_id:
            continue
        group_therapist_ids.setdefault(int(group_id), set()).add(int(payout_row.therapist_id))
        group_child_bookings.setdefault((int(group_id), int(payout_booking.patient_id)), []).append(payout_booking)
    def split_group_money(total, allocation_keys, current_key):
        ordered_keys = sorted(allocation_keys)
        count = max(1, len(ordered_keys))
        cents = int(round(float(total or 0) * 100))
        base_cents, extra_cents = divmod(cents, count)
        index = ordered_keys.index(current_key) if current_key in ordered_keys else 0
        return (base_cents + (1 if index < extra_cents else 0)) / 100
    detailed = []
    for row in rows:
        booking = bookings.get(row.patient_slot_booking_id)
        parent = crt_parents.get(booking.crt_program_booking_id) if booking and booking.crt_program_booking_id else None
        group_id = getattr(booking, "group_program_booking_id", None) if booking else None
        if group_id:
            # The configured group price is charged per enrolled child. Split
            # each child's due and payment between the therapists assigned to
            # that group session; do not collapse all children into one invoice.
            child_siblings = group_child_bookings.get(
                (int(group_id), int(booking.patient_id)), []
            )
            group_due = max(
                (float(getattr(sibling, "amount", 0) or 0) for sibling in child_siblings),
                default=float(getattr(booking, "amount", 0) or 0),
            )
            group_received = min(
                group_due,
                max(0, sum(
                    float(getattr(sibling, "paid_amount", 0) or 0)
                    + float(getattr(sibling, "package_covered_amount", 0) or 0)
                    for sibling in child_siblings
                )),
            )
            allocation_keys = group_therapist_ids.get(
                int(group_id), {int(row.therapist_id)}
            )
            current_key = int(row.therapist_id)
            due = money(split_group_money(group_due, allocation_keys, current_key))
            received = money(split_group_money(group_received, allocation_keys, current_key))
            marked_fully_paid = any(
                bool(getattr(sibling, "fully_paid", False))
                or any(
                    bool(getattr(payment, "fully_paid", False))
                    for payment in payments_by_booking.get(int(sibling.id), [])
                )
                for sibling in child_siblings
            )
        else:
            booking_payments = payments_by_booking.get(int(row.patient_slot_booking_id), [])
            ledger_due = max((float(payment.amount or 0) for payment in booking_payments), default=0.0)
            ledger_received = sum(float(payment.payment_amount or 0) for payment in booking_payments)
            stored_due = float((parent.total_amount if parent else getattr(booking, "amount", 0)) or 0)
            # Payment history can preserve a newer corrected session price than
            # the booking cache. Use the highest recorded charge so reports do
            # not fall back to an older amount (for example 1100 instead of 1200).
            due = money(max(stored_due, ledger_due))
            cached_received = max(0, float((parent.paid_amount if parent else getattr(booking, "paid_amount", 0)) or 0) + float((parent.package_covered_amount if parent else getattr(booking, "package_covered_amount", 0)) or 0))
            received = money(min(due, max(cached_received, ledger_received)))
            marked_fully_paid = bool(
                getattr(parent, "fully_paid", False)
                or getattr(booking, "fully_paid", False)
                or any(bool(getattr(payment, "fully_paid", False)) for payment in booking_payments)
            )
        # A manually completed payment can waive the unpaid remainder. Keep
        # actual receipts unchanged, but never report the waived portion as due.
        balance = 0.0 if marked_fully_paid else money(max(0, due - received))
        config = payout_configs.get(row.config_id)
        is_vocational_report_row = "vocational" in str(programs.get(row.program_id, "") or "").lower()
        is_structured_report_row = any(value in str(programs.get(row.program_id, "") or "").lower() for value in ("crt", "structured"))
        gst, net_amount = inclusive_gst(received, 0 if is_vocational_report_row or is_structured_report_row else (config.gst_rate if config else 0))
        collection_ratio = min(1.0, received / due) if due > 0 else 0.0
        try:
            calculation = json.loads(row.calculation_json or "{}")
        except (TypeError, ValueError):
            calculation = {}
        stored_ratio = calculation.get("collectionRatio")
        is_parent_payout_preview = (
            calculation.get("method") == "parent_40_percent_pool"
            and row.eligibility_status == "pending_completion"
        )
        if is_parent_payout_preview:
            configured_pool_share = float(
                config.therapist_pool_percentage
                if config and config.therapist_pool_percentage is not None
                else 0
            )
            therapist_payout = money(net_amount * 0.40 * configured_pool_share / 100)
        elif stored_ratio is None:
            therapist_payout = money(row.therapist_amount * collection_ratio)
        elif float(stored_ratio or 0) > 0:
            therapist_payout = money(row.therapist_amount * collection_ratio / float(stored_ratio))
        else:
            therapist_payout = 0.0
        session_key = f"group:{booking.group_program_booking_id}" if booking and booking.group_program_booking_id else f"booking:{booking.id if booking else row.patient_slot_booking_id}"
        booking_payments = payments_by_booking.get(int(row.patient_slot_booking_id), [])
        report_patient_id = booking.patient_id if booking else (booking_payments[-1].patient_id if booking_payments else None)
        child_name = patients.get(report_patient_id, f"Child {report_patient_id}") if report_patient_id else "—"
        detailed.append({"id": row.id, "bookingId": row.patient_slot_booking_id, "sessionKey": session_key, "date": row.session_date.isoformat(), "therapistId": row.therapist_id, "therapist": therapists.get(row.therapist_id).name if therapists.get(row.therapist_id) else f"Therapist {row.therapist_id}", "child": child_name, "program": programs.get(row.program_id, "—"), "amountDue": due, "amountReceived": received, "balance": balance, "gst": gst, "netAmount": net_amount, "therapistPayout": therapist_payout})
    payment_earning_query = db.query(TherapistPaymentEarning).filter(
        TherapistPaymentEarning.earning_date.between(start, end),
        TherapistPaymentEarning.status == "eligible",
        TherapistPaymentEarning.deleted_at.is_(None),
    )
    if scoped:
        payment_earning_query = payment_earning_query.filter(TherapistPaymentEarning.therapist_id == scoped.id)
    elif therapist_id:
        payment_earning_query = payment_earning_query.filter(TherapistPaymentEarning.therapist_id == therapist_id)
    payment_earnings = payment_earning_query.all()
    if region_id:
        allowed_ids = {row[0] for row in db.query(Therapist.id).filter(Therapist.region_id == region_id).all()}
        payment_earnings = [row for row in payment_earnings if row.therapist_id in allowed_ids]
    payment_booking_therapists = {(row.patient_slot_booking_id, row.therapist_id) for row in payment_earnings}
    earning_therapist_ids = {row.therapist_id for row in payment_earnings}
    earning_program_ids = {row.program_id for row in payment_earnings}
    if earning_therapist_ids:
        therapists.update({row.id: row for row in db.query(Therapist).filter(Therapist.id.in_(earning_therapist_ids)).all()})
    if earning_program_ids:
        programs.update({row.id: row.program_name for row in db.query(Program).filter(Program.id.in_(earning_program_ids)).all()})
    payment_earnings_by_therapist = {}
    payment_earnings_by_therapist_program = {}
    payment_earnings_by_booking_therapist = {}
    payment_activity_by_therapist_program = {}
    for earning in payment_earnings:
        payment_earnings_by_therapist[earning.therapist_id] = money(payment_earnings_by_therapist.get(earning.therapist_id, 0) + float(earning.therapist_amount or 0))
        key = (earning.therapist_id, earning.program_id)
        payment_earnings_by_therapist_program[key] = money(payment_earnings_by_therapist_program.get(key, 0) + float(earning.therapist_amount or 0))
        activity = payment_activity_by_therapist_program.setdefault(key, {
            "bookingIds": set(), "received": 0.0, "gst": 0.0,
            "netAmount": 0.0, "therapistPayout": 0.0,
        })
        activity["bookingIds"].add(earning.patient_slot_booking_id)
        activity["received"] = money(activity["received"] + float(earning.received_amount or 0))
        activity["gst"] = money(activity["gst"] + float(earning.gst_amount or 0))
        activity["netAmount"] = money(activity["netAmount"] + float(earning.amount_after_gst or 0))
        activity["therapistPayout"] = money(activity["therapistPayout"] + float(earning.therapist_amount or 0))
        booking_key = (earning.patient_slot_booking_id, earning.therapist_id)
        payment_earnings_by_booking_therapist[booking_key] = money(
            payment_earnings_by_booking_therapist.get(booking_key, 0)
            + float(earning.therapist_amount or 0)
        )

    # A billing row belongs to the session month, while therapist earnings
    # belong to the month in which the payment was actually received. Once a
    # booking is managed by the payment ledger, never fall back to the legacy
    # session-date payout for another month.
    detailed_booking_ids = {item["bookingId"] for item in detailed}
    detailed_therapist_ids = {item["therapistId"] for item in detailed}
    all_payment_earning_keys = set()
    if detailed_booking_ids and detailed_therapist_ids:
        all_payment_earning_keys = {
            (booking_id, earning_therapist_id)
            for booking_id, earning_therapist_id in db.query(
                TherapistPaymentEarning.patient_slot_booking_id,
                TherapistPaymentEarning.therapist_id,
            ).filter(
                TherapistPaymentEarning.patient_slot_booking_id.in_(detailed_booking_ids),
                TherapistPaymentEarning.therapist_id.in_(detailed_therapist_ids),
                TherapistPaymentEarning.deleted_at.is_(None),
            ).distinct().all()
        }

    detailed_by_booking_therapist = {}
    for item in detailed:
        detailed_by_booking_therapist.setdefault(
            (item["bookingId"], item["therapistId"]), []
        ).append(item)
    for booking_key, items in detailed_by_booking_therapist.items():
        uses_payment_ledger = booking_key in all_payment_earning_keys
        is_parent_program = any(
            "parent" in str(item["program"] or "").lower() for item in items
        )
        if not uses_payment_ledger and not is_parent_program:
            continue
        selected_month_total = payment_earnings_by_booking_therapist.get(booking_key, 0.0)
        allocation_keys = list(range(len(items)))
        for index, item in enumerate(items):
            item["therapistPayout"] = money(
                split_group_money(selected_month_total, allocation_keys, index)
            )
    def summarize(key):
        result = {}
        for item in detailed:
            group_key = (item[key], item["program"]); value = result.setdefault(group_key, {"name": item[key], "program": item["program"], "count": 0, "_sessionKeys": set(), "amountDue": 0, "amountReceived": 0, "balance": 0, "gst": 0, "netAmount": 0, "therapistPayout": 0})
            value["_sessionKeys"].add(item["sessionKey"])
            value["count"] = len(value["_sessionKeys"])
            for field in ("amountDue", "amountReceived", "balance", "gst", "netAmount", "therapistPayout"): value[field] = money(value[field] + item[field])
        for value in result.values(): value.pop("_sessionKeys", None)
        return list(result.values())
    therapist_summary = summarize("therapist")
    summary_by_therapist_program = {}
    for item in therapist_summary:
        therapist_row = next((row for row in therapists.values() if row.name == item["name"]), None)
        program_id = next((pid for pid, name in programs.items() if name == item["program"]), None)
        if therapist_row and program_id:
            summary_by_therapist_program[(therapist_row.id, program_id)] = item
            if (therapist_row.id, program_id) in payment_activity_by_therapist_program:
                item["therapistPayout"] = payment_earnings_by_therapist_program.get((therapist_row.id, program_id), 0.0)
    # Include older sessions paid in the selected month in Therapist Summary.
    for key, activity in payment_activity_by_therapist_program.items():
        if key in summary_by_therapist_program:
            continue
        earning_therapist_id, earning_program_id = key
        received = money(activity["received"])
        therapist_summary.append({
            "name": therapists[earning_therapist_id].name,
            "program": programs.get(earning_program_id, f"Program {earning_program_id}"),
            "count": len(activity["bookingIds"]),
            "amountDue": received,
            "amountReceived": received,
            "balance": 0.0,
            "gst": money(activity["gst"]),
            "netAmount": money(activity["netAmount"]),
            "therapistPayout": money(activity["therapistPayout"]),
            "paymentActivityOnly": True,
        })
    payroll = []
    payroll_therapist_ids = {item["therapistId"] for item in detailed} | set(payment_earnings_by_therapist)
    for tid in sorted(payroll_therapist_ids):
        non_ledger_session_earnings = sum(item["therapistPayout"] for item in detailed if item["therapistId"] == tid and (item["bookingId"], tid) not in payment_booking_therapists and "parent" not in str(item["program"] or "").lower())
        earnings = money(non_ledger_session_earnings + payment_earnings_by_therapist.get(tid, 0)); config = applicable_payroll_config(db, tid, end)
        if config and config.fixed_salary_only:
            earnings = 0.0
        base = money(config.base_salary if config and config.fixed_salary_only else 0); flat = 0.0; gross = money(base + earnings); pt = professional_tax_for(db, gross, start)
        payroll.append({"therapistId": tid, "therapist": therapists[tid].name, "sessionEarnings": earnings, "baseSalary": base, "flatCommission": flat, "professionalTax": pt, "fixedSalaryOnly": bool(config.fixed_salary_only) if config else False, "gross": gross, "netPay": money(gross - pt), "effectiveFrom": config.effective_from.isoformat() if config else None})
    totals = {field: money(sum(item[field] for item in detailed)) for field in ("amountDue", "amountReceived", "balance", "gst", "netAmount", "therapistPayout")}
    totals["therapistPayout"] = money(sum(item["therapistPayout"] for item in detailed if (item["bookingId"], item["therapistId"]) not in payment_booking_therapists and "parent" not in str(item["program"] or "").lower()) + sum(payment_earnings_by_therapist.values()))
    return {"data": {"month": month, "detailed": detailed, "children": summarize("child"), "therapists": therapist_summary, "payroll": payroll, "totals": totals}}


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
    created_sheets = calculated = reconciled = skipped = 0
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
        payroll_config = applicable_payroll_config(db, mapping.therapist_id, mapping.slot_date)
        fixed_salary_only = bool(payroll_config and payroll_config.fixed_salary_only)
        config = applicable_config(db, mapping.therapist_id, row.program_id, mapping.slot_date)
        if not config and fixed_salary_only:
            # Fixed-salary therapists do not need editable per-program earning
            # rules, but completed sessions must still enter the billing ledger.
            # Create an internal zero-value rule so the payout FK remains valid
            # and the billing/child/therapist summaries retain the session.
            program_name = str(row.program.program_name if row.program else "").lower()
            is_non_gst_program = any(value in program_name for value in ("vocational", "crt", "structured"))
            config = ProgramPaymentConfig(
                therapist_id=mapping.therapist_id,
                program_id=row.program_id,
                payment_method="fixed",
                percentage=None,
                fixed_amount=0,
                gst_rate=0 if is_non_gst_program else 18,
                therapist_pool_percentage=0,
                allocation_rule="equal_child_equivalent",
                release_on_completion=False,
                effective_from=payroll_config.effective_from,
                is_active=True,
                created_by=user.id,
                updated_by=user.id,
            )
            db.add(config)
            db.flush()
        if not config:
            skipped += 1; continue
        existing_payout = db.query(TherapistSessionPayout).filter_by(patient_slot_booking_id=row.id, therapist_id=mapping.therapist_id).first()
        existing_monthly = db.get(TherapistMonthlyPayout, existing_payout.monthly_payout_id) if existing_payout and existing_payout.monthly_payout_id else None
        if existing_monthly and existing_monthly.status != "draft":
            # Approved and paid monthly payroll is a finalized financial record.
            skipped += 1
            continue
        eligible = money(row.amount)
        package = row.patient_package.package if row.patient_package else None
        child_pricing = applicable_patient_pricing(db, row.patient_id, row.program_id, mapping.slot_date)
        if child_pricing:
            eligible = money(child_pricing.agreed_amount / child_pricing.total_sessions) if child_pricing.billing_type == "package" and child_pricing.total_sessions else money(child_pricing.agreed_amount)
        therapist_count = 1
        child_equivalent = 1.0
        group_rows = []
        parent_program_group = None
        group_session_amount = 0.0
        if row.group_program_booking_id:
            group_rows = db.query(PatientSlotBooking).options(joinedload(PatientSlotBooking.therapist_slot_mapping)).filter(PatientSlotBooking.group_program_booking_id == row.group_program_booking_id).all()
            group_patient_ids = {int(x.patient_id) for x in group_rows if x.patient_id}
            linked_parent_group_id = getattr(row.group_program_booking, "parent_program_group_id", None)
            if linked_parent_group_id:
                parent_program_group = db.execute(text("SELECT id,start_date,end_date,status,pro_rata_amount,number_of_sessions FROM parent_program_groups WHERE id=:group_id"), {"group_id": linked_parent_group_id}).mappings().first()
            if not parent_program_group:
                candidates = db.execute(text("SELECT id,start_date,end_date,status,pro_rata_amount,number_of_sessions FROM parent_program_groups WHERE program_id=:program_id AND start_date<=:slot_date AND end_date>=:slot_date"), {"program_id": row.program_id, "slot_date": mapping.slot_date}).mappings().all()
                for candidate in candidates:
                    member_ids = {int(value[0]) for value in db.execute(text("SELECT patient_id FROM parent_program_group_members WHERE group_id=:group_id"), {"group_id": candidate["id"]}).all()}
                    if member_ids and member_ids == group_patient_ids:
                        parent_program_group = candidate
                        break
            if parent_program_group and int(parent_program_group.get("number_of_sessions") or 0) > 0:
                group_session_amount = money(float(parent_program_group.get("pro_rata_amount") or 0) / int(parent_program_group["number_of_sessions"]))
            # Keep group_rows scoped to this exact session. The linked parent
            # program period may be historical; replacing current rows with
            # older matching-member sessions loses today's payment receipts.
            group_therapist_ids = {x.therapist_slot_mapping.therapist_id for x in group_rows if x.therapist_slot_mapping}
            therapist_count = max(1, len(group_therapist_ids))
            child_equivalent = len({x.patient_id for x in group_rows if x.patient_id}) / therapist_count
            eligible = money((row.amount or (package.price / package.total_sessions if package and package.total_sessions else 0)) * child_equivalent)
        payment_parent = row.crt_program_booking
        if payment_parent:
            session_amount = float(payment_parent.total_amount or row.amount or 0)
            # Read CRT cash receipts from the payment ledger so the payout table
            # cannot keep taxing the full session amount when the parent summary
            # has not yet been refreshed after a partial payment.
            crt_slot_ids = [
                int(item.id)
                for item in (payment_parent.slot_bookings or [])
                if item.id
            ]
            receipt_filters = [Payment.crt_program_booking_id == payment_parent.id]
            if crt_slot_ids:
                receipt_filters.append(Payment.patient_slot_booking_id.in_(crt_slot_ids))
                receipt_filters.extend(Payment.remark.like(f"[slot:{slot_id}]%") for slot_id in crt_slot_ids)
            ledger_received = float(
                db.query(func.coalesce(func.sum(Payment.payment_amount), 0))
                .filter(or_(*receipt_filters))
                .scalar()
                or 0
            )
            # The payment ledger is the source of truth for cash collected.
            # Do not fall back to the CRT booking's cached paid_amount: stale
            # values there can make an unpaid session appear fully collected.
            session_received = ledger_received + float(payment_parent.package_covered_amount or 0)
        elif row.group_program_booking_id:
            patient_amounts = {}
            patient_receipts = {}
            for item in group_rows:
                if not item.patient_id:
                    continue
                patient_amounts[item.patient_id] = max(patient_amounts.get(item.patient_id, 0.0), float(item.amount or 0))
                patient_receipts[item.patient_id] = max(patient_receipts.get(item.patient_id, 0.0), float(item.paid_amount or 0) + float(item.package_covered_amount or 0))
            # Parent-program group pricing is per child. Build the session
            # total from all enrolled children, falling back to the configured
            # per-child session price when booking amounts are not populated.
            child_count = max(1, len(patient_amounts))
            session_amount = sum(patient_amounts.values()) or (group_session_amount * child_count)
            session_received = sum(patient_receipts.values())
            if parent_program_group:
                program_slot_ids = [item.id for item in group_rows if item.id]
                ledger_received = float(db.query(func.coalesce(func.sum(Payment.payment_amount), 0)).filter(Payment.patient_slot_booking_id.in_(program_slot_ids)).scalar() or 0)
                session_received = max(session_received, ledger_received)
        else:
            session_amount = float(row.amount or 0)
            session_received = float(row.paid_amount or 0) + float(row.package_covered_amount or 0)
        session_received = min(max(0.0, session_amount), max(0.0, session_received))
        paid_ratio = min(1.0, session_received / session_amount) if session_amount > 0 else 0.0
        if payment_parent:
            eligible = money(session_amount)
        program_name = str(row.program.program_name if row.program else "").lower()
        is_structured_program = "crt" in program_name or "structured" in program_name
        is_parent_program = "parent" in program_name
        is_vocational_program = "vocational" in program_name
        gst_rate = 0.0 if is_vocational_program or is_structured_program else float(config.gst_rate or 0)
        is_group_payout_row = True
        if row.group_program_booking_id:
            therapist_group_rows = [item for item in group_rows if item.therapist_slot_mapping and item.therapist_slot_mapping.therapist_id == mapping.therapist_id]
            canonical_group_row_id = max(therapist_group_rows, key=lambda item: (item.therapist_slot_mapping.slot_date, item.id)).id if therapist_group_rows else row.id
            is_group_payout_row = row.id == canonical_group_row_id
        if is_parent_program and package and package.total_sessions:
            eligible = money(package.price / package.total_sessions / therapist_count)
        elif config.payment_method == "package" and package and package.total_sessions:
            eligible = money(package.price / package.total_sessions / therapist_count)
        if row.group_program_booking_id and is_parent_program:
            # Allocate the cash actually received for this child-priced
            # session equally across its therapists. Partial collections earn
            # proportionally and later payments are picked up by auto-sync.
            eligible = money(session_amount * paid_ratio / therapist_count) if is_group_payout_row and session_amount > 0 else 0.0
        else:
            eligible = money(eligible * paid_ratio)
        gst, after_gst = inclusive_gst(eligible, gst_rate)
        duration_minutes = int(row.duration_minutes or 0)
        if is_structured_program:
            structured_rates = {
                45: float(config.fixed_amount if config.fixed_amount is not None else 300),
                30: float(config.therapist_pool_percentage if config.therapist_pool_percentage is not None else 100),
            }
            therapist_amount = money(structured_rates.get(duration_minutes, 0) * paid_ratio)
        elif is_parent_program:
            # Parent Programs first create a 40% therapist pool after GST.
            # Group collections have already been divided equally between the
            # session therapists; each therapist's configured percentage is
            # then applied to that therapist's share of the 40% pool.
            parent_pool_percentage = float(config.therapist_pool_percentage if config.therapist_pool_percentage is not None else 0)
            therapist_amount = money(after_gst * 0.40 * parent_pool_percentage / 100)
        elif is_vocational_program:
            # Vocational earnings are fixed at Rs 529 for the whole session.
            # Split that amount equally between every attending therapist.
            therapist_amount = money(529 * paid_ratio / therapist_count)
        elif config.payment_method == "package":
            therapist_amount = money(after_gst * float(config.therapist_pool_percentage if config.therapist_pool_percentage is not None else 50) / 100)
        else:
            if config.fixed_amount is not None and config.payment_method in {"fixed", "completion"}:
                therapist_amount = money(config.fixed_amount * paid_ratio)
            else:
                therapist_amount = money(after_gst * float(config.percentage or 0) / 100)
        if fixed_salary_only:
            therapist_amount = 0.0
        status = "pending_payment" if session_received <= 0 or not is_group_payout_row else "eligible"
        if is_parent_program and parent_program_group and str(parent_program_group.get("status") or "").upper() != "COMPLETED" and date.today() < parent_program_group["end_date"]:
            status = "pending_completion"
        if session_received > 0 and config.release_on_completion and not (row.group_program_booking_id and is_parent_program):
            package_complete = bool(row.patient_package and (row.patient_package.sessions_remaining <= 0 or str(row.patient_package.status).lower() == "completed"))
            status = "eligible" if package_complete else "pending_completion"
        allocation = db.query(TherapistSessionAllocation).filter_by(patient_slot_booking_id=row.id, therapist_id=mapping.therapist_id).first()
        if not allocation:
            db.add(TherapistSessionAllocation(patient_slot_booking_id=row.id, therapist_id=mapping.therapist_id, child_equivalent=child_equivalent, allocation_percentage=100 / therapist_count))
        pool_allocation_percentage = (100 / therapist_count) if is_parent_program else None
        configured_pool_share = float(config.therapist_pool_percentage if config.therapist_pool_percentage is not None else 0) if is_parent_program else None
        breakdown = {"method": "fixed_salary_only" if fixed_salary_only else "structured_duration_fixed" if is_structured_program else "parent_40_percent_pool" if is_parent_program else "vocational_fixed_529_split" if is_vocational_program else config.payment_method, "configId": config.id, "gstRate": gst_rate, "amountReceived": session_received, "collectionRatio": paid_ratio, "childEquivalent": child_equivalent, "therapistCount": therapist_count, "durationMinutes": duration_minutes, "fixedSessionAmount": 529 if is_vocational_program else None, "fixedSalaryOnly": fixed_salary_only, "organizationPercentage": 60 if is_parent_program else None, "therapistPoolPercentage": 40 if is_parent_program else config.therapist_pool_percentage, "therapistPoolAllocationPercentage": pool_allocation_percentage, "therapistConfiguredPoolSharePercentage": configured_pool_share, "groupSessionAmount": group_session_amount if row.group_program_booking_id else None, "gstAlreadyDeducted": not is_vocational_program}
        payout_values = {"program_id": row.program_id, "config_id": config.id, "session_date": mapping.slot_date, "eligible_amount": eligible, "gst_amount": gst, "amount_after_gst": after_gst, "therapist_percentage": configured_pool_share if is_parent_program else config.percentage, "therapist_amount": therapist_amount, "organization_amount": money(after_gst - therapist_amount), "calculation_json": json.dumps(breakdown), "eligibility_status": status}
        if existing_payout:
            for field, value in payout_values.items():
                setattr(existing_payout, field, value)
            if existing_monthly:
                month_end = existing_monthly.payout_month.replace(day=monthrange(existing_monthly.payout_month.year, existing_monthly.payout_month.month)[1])
                month_rows = db.query(TherapistSessionPayout).filter_by(monthly_payout_id=existing_monthly.id).all()
                payroll_config = applicable_payroll_config(db, existing_monthly.therapist_id, month_end)
                session_earnings = money(sum(item.therapist_amount for item in month_rows))
                if payroll_config and payroll_config.fixed_salary_only:
                    session_earnings = 0.0
                gross_before_tax = money(session_earnings + (payroll_config.base_salary if payroll_config and payroll_config.fixed_salary_only else 0))
                existing_monthly.gross_amount = money(gross_before_tax - professional_tax_for(db, gross_before_tax, existing_monthly.payout_month))
                existing_monthly.payable_amount = money(existing_monthly.gross_amount + existing_monthly.adjustment_amount)
            reconciled += 1
        else:
            db.add(TherapistSessionPayout(patient_slot_booking_id=row.id, therapist_id=mapping.therapist_id, **payout_values))
            calculated += 1
    payment_result = sync_payment_earnings(db, start_date, end_date, region_id)
    db.commit()
    return {"data": {"sheetAssignmentsCreated": created_sheets, "payoutsCalculated": calculated, "payoutsReconciled": reconciled, "skipped": skipped, "paymentEarningsCreated": payment_result["created"], "paymentEarningsUpdated": payment_result["updated"], "paymentEarningsSkipped": payment_result["skipped"]}}


@router.post("/monthly/generate")
def generate_month(payload: dict, db: Session = Depends(get_db), user: User = Depends(get_payout_manager)):
    try:
        start = datetime.strptime(payload.get("month", ""), "%Y-%m").date().replace(day=1)
    except ValueError:
        raise HTTPException(400, "month must use YYYY-MM")
    end = start.replace(day=monthrange(start.year, start.month)[1])
    parent_program_ids = [row[0] for row in db.query(Program.id).filter(func.lower(Program.program_name).like("%parent%"), Program.deleted_at.is_(None)).all()]
    session_query = db.query(TherapistSessionPayout).filter(
        TherapistSessionPayout.session_date.between(start, end),
        TherapistSessionPayout.eligibility_status == "eligible",
        TherapistSessionPayout.deleted_at.is_(None),
    )
    if parent_program_ids:
        session_query = session_query.filter(~TherapistSessionPayout.program_id.in_(parent_program_ids))
    monthly_ledger_exists = db.query(TherapistPaymentEarning.id).filter(
        TherapistPaymentEarning.patient_slot_booking_id == TherapistSessionPayout.patient_slot_booking_id,
        TherapistPaymentEarning.therapist_id == TherapistSessionPayout.therapist_id,
        TherapistPaymentEarning.deleted_at.is_(None),
    ).exists()
    session_query = session_query.filter(~monthly_ledger_exists)
    payment_query = db.query(TherapistPaymentEarning).filter(
        TherapistPaymentEarning.earning_date.between(start, end),
        TherapistPaymentEarning.status == "eligible",
        TherapistPaymentEarning.deleted_at.is_(None),
    )
    unassigned_sessions = session_query.filter(TherapistSessionPayout.monthly_payout_id.is_(None)).all()
    unassigned_payments = payment_query.filter(TherapistPaymentEarning.monthly_payout_id.is_(None)).all()
    therapist_ids = {row.therapist_id for row in unassigned_sessions + unassigned_payments}
    therapist_ids.update(row[0] for row in db.query(TherapistPayrollConfig.therapist_id).filter(
        TherapistPayrollConfig.is_active.is_(True), TherapistPayrollConfig.deleted_at.is_(None),
        TherapistPayrollConfig.effective_from <= end,
        or_(TherapistPayrollConfig.effective_to.is_(None), TherapistPayrollConfig.effective_to >= end),
    ).distinct().all())
    therapist_ids.update(row[0] for row in db.query(TherapistMonthlyPayout.therapist_id).filter(
        TherapistMonthlyPayout.payout_month == start, TherapistMonthlyPayout.status == "draft",
        TherapistMonthlyPayout.deleted_at.is_(None),
    ).all())
    generated = 0
    for therapist_id in therapist_ids:
        monthly = db.query(TherapistMonthlyPayout).filter_by(therapist_id=therapist_id, payout_month=start).first()
        if not monthly:
            monthly = TherapistMonthlyPayout(therapist_id=therapist_id, payout_month=start, gross_amount=0, payable_amount=0)
            db.add(monthly)
            db.flush()
            generated += 1
        if monthly.status != "draft":
            continue
        session_rows = session_query.filter(
            TherapistSessionPayout.therapist_id == therapist_id,
            or_(TherapistSessionPayout.monthly_payout_id.is_(None), TherapistSessionPayout.monthly_payout_id == monthly.id),
        ).all()
        payment_rows = payment_query.filter(
            TherapistPaymentEarning.therapist_id == therapist_id,
            or_(TherapistPaymentEarning.monthly_payout_id.is_(None), TherapistPaymentEarning.monthly_payout_id == monthly.id),
        ).all()
        session_earnings = money(sum(row.therapist_amount for row in session_rows) + sum(row.therapist_amount for row in payment_rows))
        payroll_config = applicable_payroll_config(db, therapist_id, end)
        if payroll_config and payroll_config.fixed_salary_only:
            session_earnings = 0.0
        gross_before_tax = money(session_earnings + (payroll_config.base_salary if payroll_config and payroll_config.fixed_salary_only else 0))
        gross = money(gross_before_tax - professional_tax_for(db, gross_before_tax, start))
        adjustment = money(sum(row.amount for row in db.query(TherapistPayoutAdjustment).filter_by(monthly_payout_id=monthly.id).all()))
        monthly.gross_amount = gross
        monthly.adjustment_amount = adjustment
        monthly.payable_amount = money(gross + adjustment)
        for earning in session_rows + payment_rows:
            if earning.monthly_payout_id is None:
                earning.monthly_payout_id = monthly.id
    db.commit()
    return {"data": {"generated": generated, "sessionEarningsIncluded": len(unassigned_sessions), "paymentEarningsIncluded": len(unassigned_payments)}}

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
