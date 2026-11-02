from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, inspect, or_
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.api.routes.ui import _require_user, _permission_shape, _user_region_ids, _user_roles
from app.models.models import (PatientSlotBooking, TherapistSlotMapping, Therapist, Patient,
                               CrtProgramBooking, GroupProgramBooking, PatientPackage, Package,
                               PatientAssessment, AssessmentMaster)
from app.models.payouts import TherapySheetAssignment
from app.services.program_operations import combine_booking_rows, summarize_operations

router = APIRouter(prefix='/api/v1/ui/reports/program-operations', tags=['program operations'])


@router.get('')
def program_operations(request: Request, start_date: date, end_date: date,
                       region_id: int | None = None, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    roles = {str(role).lower().replace('-', '_').replace(' ', '_') for role in _user_roles(db, user)}
    privileged = bool(roles & {'admin', 'front_office', 'frontoffice', 'front_officer', 'central_head', 'centralhead'})
    if not privileged and not _permission_shape(db, user).get('menu.reports', {}).get('view', False):
        raise HTTPException(403, 'You do not have permission to view operational reports.')
    ids = _user_region_ids(db, user)
    if not ids and not roles & {'admin', 'central_head', 'centralhead'}:
        raise HTTPException(403, 'No centre is assigned to your account.')
    if region_id and ids and region_id not in ids:
        raise HTTPException(403, 'Centre is outside your access.')
    if start_date > end_date:
        raise HTTPException(422, 'From date must be before or equal to To date.')
    if (end_date - start_date).days > 366:
        raise HTTPException(422, 'Select a report period of up to one year.')
    scope = [region_id] if region_id else ids
    own = db.query(Therapist).filter(Therapist.user_id == user.id).first() if not privileged else None
    if not privileged and any('therapist' in role for role in roles) and own is None:
        raise HTTPException(403, 'No therapist is linked to your account.')
    query = db.query(PatientSlotBooking).join(TherapistSlotMapping,
        TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id).join(Therapist,
        Therapist.id == TherapistSlotMapping.therapist_id).outerjoin(Patient,
        Patient.id == PatientSlotBooking.patient_id).filter(
        TherapistSlotMapping.slot_date.between(start_date, end_date)).options(
        joinedload(PatientSlotBooking.patient), joinedload(PatientSlotBooking.program),
        joinedload(PatientSlotBooking.patient_slot_booking_status_master),
        joinedload(PatientSlotBooking.crt_program_booking).joinedload(CrtProgramBooking.program),
        joinedload(PatientSlotBooking.group_program_booking).joinedload(GroupProgramBooking.program),
        joinedload(PatientSlotBooking.patient_package).joinedload(PatientPackage.package).joinedload(Package.program),
        joinedload(PatientSlotBooking.therapist_slot_mapping).joinedload(TherapistSlotMapping.therapist))
    if scope:
        query = query.filter(or_(Patient.region_id.in_(scope),
                                 (PatientSlotBooking.patient_id.is_(None) & Therapist.region_id.in_(scope))))
    if own:
        query = query.filter(TherapistSlotMapping.therapist_id == own.id)
    bookings = query.all()
    pending = None
    if inspect(db.get_bind()).has_table('therapy_sheet_assignment'):
        pending = {}
        booking_ids = [row.id for row in bookings]
        # Keep IN clauses below common database parameter limits.
        for start in range(0, len(booking_ids), 500):
            assignments = db.query(TherapySheetAssignment.patient_slot_booking_id, func.count()).filter(
                TherapySheetAssignment.patient_slot_booking_id.in_(booking_ids[start:start + 500]),
                TherapySheetAssignment.deleted_at.is_(None),
                TherapySheetAssignment.status.in_(['pending', 'assigned', 'overdue'])).group_by(
                    TherapySheetAssignment.patient_slot_booking_id).all()
            pending.update(dict(assignments))
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    rows = combine_booking_rows(bookings, pending, today)
    if not own:
        activity_date = func.date(func.coalesce(PatientAssessment.completed_date, PatientAssessment.created_date))
        assessments = db.query(PatientAssessment, Patient, AssessmentMaster).join(
            Patient, Patient.id == PatientAssessment.patient_id).join(
            AssessmentMaster, AssessmentMaster.id == PatientAssessment.assessment_id).options(
            joinedload(PatientAssessment.patient_assessment_status_master)).filter(activity_date.between(start_date, end_date))
        if scope:
            assessments = assessments.filter(Patient.region_id.in_(scope))
        for assessment, patient, master in assessments.all():
            code = str(assessment.patient_assessment_status_master.code if assessment.patient_assessment_status_master else assessment.status).upper()
            status = {'COMPLETED': 'Completed', 'CANCELLED': 'Cancelled', 'IN_PROGRESS': 'In progress'}.get(code, 'Pending')
            day = assessment.completed_date or assessment.created_date
            rows.append({'key': f'assessment:{assessment.id}', 'date': day.date().isoformat(),
                         'category': 'Assessment', 'program': master.assessment_name or master.title,
                         'activity': 'Assessment record', 'patient_id': patient.id,
                         'child': f'{patient.first_name} {patient.last_name}'.strip(),
                         'therapist_ids': [], 'therapist_options': [], 'therapists': 'Not recorded', 'region_id': patient.region_id,
                         'status': status, 'recorded_status': code, 'pending_sheets': None})
    rows.sort(key=lambda row: (row['date'], row['key']), reverse=True)
    return {'data': {'rows': rows, 'summary': summarize_operations(rows),
                     'start_date': start_date, 'end_date': end_date,
                     'sheets_available': pending is not None,
                     'therapist_scoped': own is not None}}
