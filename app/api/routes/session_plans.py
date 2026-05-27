from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import (
    MASTER_LOOKUP_DATA,
    Patient,
    PatientSessionPlan,
    PatientSessionPlanItem,
    TherapyMaster,
    User,
)
from app.schemas.schemas import PatientSessionPlanCreate, PatientSessionPlanUpdate

router = APIRouter(prefix="/api/v1/session-plans", tags=["session-plans"])

PLAN_STATUS_ACTIVE_ID = MASTER_LOOKUP_DATA["patient_session_plan"]["ACTIVE"]
PLAN_STATUS_CANCELLED_ID = MASTER_LOOKUP_DATA["patient_session_plan"]["CANCELLED"]
PLAN_STATUS_COMPLETED_ID = MASTER_LOOKUP_DATA["patient_session_plan"]["COMPLETED"]


def _plan_name(start_date: date, end_date: date) -> str:
    if start_date.year == end_date.year:
        if start_date.month == end_date.month:
            return f"{start_date.strftime('%b')} {start_date.day} - {end_date.strftime('%b')} {end_date.day}"
        return f"{start_date.strftime('%b')} {start_date.day} - {end_date.strftime('%b')} {end_date.day}"
    return f"{start_date.strftime('%b')} {start_date.day}, {start_date.year} - {end_date.strftime('%b')} {end_date.day}, {end_date.year}"


def _status_label(status_id: Optional[int]) -> str:
    return {
        PLAN_STATUS_ACTIVE_ID: "ACTIVE",
        PLAN_STATUS_CANCELLED_ID: "CANCELLED",
        PLAN_STATUS_COMPLETED_ID: "COMPLETED",
    }.get(status_id or PLAN_STATUS_ACTIVE_ID, "ACTIVE")


def _shape_plan(plan: PatientSessionPlan) -> dict:
    items = []
    for item in plan.items:
        used = (item.assigned_sessions or 0) + (item.completed_sessions or 0)
        items.append({
            "id": item.id,
            "therapy_id": item.therapy_id,
            "therapy_name": item.therapy.name if item.therapy else f"Therapy #{item.therapy_id}",
            "allocated_sessions": item.allocated_sessions,
            "assigned_sessions": item.assigned_sessions or 0,
            "completed_sessions": item.completed_sessions or 0,
            "remaining_sessions": max(0, (item.allocated_sessions or 0) - used),
            "amount_per_session": float(item.amount_per_session or 0),
        })
    return {
        "id": plan.id,
        "patient_id": plan.patient_id,
        "plan_name": plan.plan_name,
        "total_sessions": plan.total_sessions or 0,
        "start_date": plan.start_date.isoformat() if plan.start_date else None,
        "end_date": plan.end_date.isoformat() if plan.end_date else None,
        "notes": plan.notes,
        "status": _status_label(plan.status_id),
        "items": items,
    }


def _assert_patient_access(db: Session, patient_id: int, current_user: User) -> Patient:
    query = db.query(Patient).filter(Patient.id == patient_id)
    region_ids = getattr(current_user, "region_ids", []) or []
    if region_ids:
        query = query.filter(Patient.region_id.in_(region_ids))
    patient = query.first()
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


@router.get("")
async def list_session_plans(
    patient_id: Optional[int] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(PatientSessionPlan)
        .options(
            joinedload(PatientSessionPlan.items).joinedload(PatientSessionPlanItem.therapy)
        )
        .order_by(PatientSessionPlan.start_date.desc(), PatientSessionPlan.id.desc())
    )
    region_ids = getattr(current_user, "region_ids", []) or []
    if patient_id:
        _assert_patient_access(db, patient_id, current_user)
        query = query.filter(PatientSessionPlan.patient_id == patient_id)
    elif region_ids:
        query = query.join(Patient, Patient.id == PatientSessionPlan.patient_id).filter(Patient.region_id.in_(region_ids))

    plans = query.all()
    return {"data": [_shape_plan(plan) for plan in plans], "total": len(plans)}


@router.get("/therapies")
async def list_therapies(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    therapies = (
        db.query(TherapyMaster)
        .filter(TherapyMaster.is_active == 1)
        .order_by(TherapyMaster.name.asc())
        .all()
    )
    return {
        "data": [
            {
                "id": therapy.id,
                "therapy_id": therapy.id,
                "therapy_name": therapy.name,
                "name": therapy.name,
            }
            for therapy in therapies
        ]
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session_plan(
    payload: PatientSessionPlanCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _assert_patient_access(db, payload.patient_id, current_user)
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="End date cannot be before start date")

    therapy_ids = [item.therapy_id for item in payload.items]
    if len(set(therapy_ids)) != len(therapy_ids):
        raise HTTPException(status_code=400, detail="Each therapy can be added only once")

    allocated_total = sum(item.allocated_sessions for item in payload.items)
    if allocated_total > payload.total_sessions:
        raise HTTPException(status_code=400, detail="Allocated therapy sessions cannot exceed total sessions")

    active_therapy_count = (
        db.query(TherapyMaster)
        .filter(TherapyMaster.id.in_(therapy_ids), TherapyMaster.is_active == 1)
        .count()
    )
    if active_therapy_count != len(therapy_ids):
        raise HTTPException(status_code=400, detail="One or more therapies are invalid")

    plan = PatientSessionPlan(
        patient_id=payload.patient_id,
        plan_name=(payload.plan_name or "").strip() or _plan_name(payload.start_date, payload.end_date),
        total_sessions=payload.total_sessions,
        start_date=payload.start_date,
        end_date=payload.end_date,
        notes=payload.notes,
        status_id=PLAN_STATUS_ACTIVE_ID,
    )
    db.add(plan)
    db.flush()

    for item in payload.items:
        db.add(PatientSessionPlanItem(
            patient_session_plan_id=plan.id,
            therapy_id=item.therapy_id,
            allocated_sessions=item.allocated_sessions,
            assigned_sessions=0,
            completed_sessions=0,
            amount_per_session=item.amount_per_session,
        ))

    db.commit()
    db.refresh(plan)
    plan = (
        db.query(PatientSessionPlan)
        .options(joinedload(PatientSessionPlan.items).joinedload(PatientSessionPlanItem.therapy))
        .filter(PatientSessionPlan.id == plan.id)
        .first()
    )
    return {"data": _shape_plan(plan)}


@router.get("/{plan_id}")
async def get_session_plan(
    plan_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = (
        db.query(PatientSessionPlan)
        .options(joinedload(PatientSessionPlan.items).joinedload(PatientSessionPlanItem.therapy))
        .filter(PatientSessionPlan.id == plan_id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Session plan not found")
    _assert_patient_access(db, plan.patient_id, current_user)
    return {"data": _shape_plan(plan)}


@router.put("/{plan_id}")
@router.patch("/{plan_id}")
async def update_session_plan(
    plan_id: int,
    payload: PatientSessionPlanUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = (
        db.query(PatientSessionPlan)
        .options(joinedload(PatientSessionPlan.items).joinedload(PatientSessionPlanItem.therapy))
        .filter(PatientSessionPlan.id == plan_id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Session plan not found")
    _assert_patient_access(db, plan.patient_id, current_user)

    next_start = payload.start_date if payload.start_date is not None else plan.start_date
    next_end = payload.end_date if payload.end_date is not None else plan.end_date
    next_total = payload.total_sessions if payload.total_sessions is not None else plan.total_sessions
    if next_start and next_end and next_end < next_start:
        raise HTTPException(status_code=400, detail="End date cannot be before start date")

    if payload.items is not None:
        therapy_ids = [item.therapy_id for item in payload.items]
        if len(set(therapy_ids)) != len(therapy_ids):
            raise HTTPException(status_code=400, detail="Each therapy can be added only once")
        allocated_total = sum(item.allocated_sessions for item in payload.items)
        if allocated_total > (next_total or 0):
            raise HTTPException(status_code=400, detail="Allocated therapy sessions cannot exceed total sessions")
        active_therapy_count = (
            db.query(TherapyMaster)
            .filter(TherapyMaster.id.in_(therapy_ids), TherapyMaster.is_active == 1)
            .count()
        )
        if active_therapy_count != len(therapy_ids):
            raise HTTPException(status_code=400, detail="One or more therapies are invalid")

        existing_by_therapy = {item.therapy_id: item for item in plan.items}
        keep_ids = set()
        for item in payload.items:
            existing = existing_by_therapy.get(item.therapy_id)
            if existing:
                existing.allocated_sessions = item.allocated_sessions
                existing.amount_per_session = item.amount_per_session
                keep_ids.add(existing.id)
            else:
                new_item = PatientSessionPlanItem(
                    patient_session_plan_id=plan.id,
                    therapy_id=item.therapy_id,
                    allocated_sessions=item.allocated_sessions,
                    assigned_sessions=0,
                    completed_sessions=0,
                    amount_per_session=item.amount_per_session,
                )
                db.add(new_item)
                db.flush()
                keep_ids.add(new_item.id)

        for existing in list(plan.items):
            if existing.id not in keep_ids:
                if (existing.assigned_sessions or 0) or (existing.completed_sessions or 0):
                    raise HTTPException(status_code=400, detail="Cannot remove therapy items with assigned or completed sessions")
                db.delete(existing)
    elif payload.total_sessions is not None:
        allocated_total = sum(item.allocated_sessions or 0 for item in plan.items)
        if allocated_total > payload.total_sessions:
            raise HTTPException(status_code=400, detail="Allocated therapy sessions cannot exceed total sessions")

    if payload.plan_name is not None:
        plan.plan_name = payload.plan_name.strip() or _plan_name(next_start, next_end)
    if payload.total_sessions is not None:
        plan.total_sessions = payload.total_sessions
    if payload.start_date is not None:
        plan.start_date = payload.start_date
    if payload.end_date is not None:
        plan.end_date = payload.end_date
    if payload.notes is not None:
        plan.notes = payload.notes
    if payload.status is not None:
        status_id = {
            "ACTIVE": PLAN_STATUS_ACTIVE_ID,
            "CANCELLED": PLAN_STATUS_CANCELLED_ID,
            "COMPLETED": PLAN_STATUS_COMPLETED_ID,
        }.get(payload.status.upper())
        if not status_id:
            raise HTTPException(status_code=400, detail="Invalid plan status")
        plan.status_id = status_id

    db.commit()
    plan = (
        db.query(PatientSessionPlan)
        .options(joinedload(PatientSessionPlan.items).joinedload(PatientSessionPlanItem.therapy))
        .filter(PatientSessionPlan.id == plan.id)
        .first()
    )
    return {"data": _shape_plan(plan)}
