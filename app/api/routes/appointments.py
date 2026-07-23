from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import date
from app.core.database import get_db
from app.dependencies.auth import get_current_user, check_region_access, get_user_roles
from app.schemas.schemas import (
    SlotBookingCreate,
    SlotBookingResponse, SlotCancelResponse, SlotMasterResponse,
    SlotStatusActionRequest, SlotStatusActionResponse
)
from app.services.appointment_service import AppointmentService, SlotMasterService
from app.models.models import Program, ProgramSegment, Therapist, User
from app.utils.logger import setup_logging

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])
logger = setup_logging(__name__)


def _normalized_roles(roles: list[str]) -> set[str]:
    return {str(role or "").strip().lower().replace(" ", "_") for role in roles}


def _is_front_office_role(roles: set[str]) -> bool:
    return bool(roles & {"admin", "front_office", "frontoffice", "front_officer"})


def _is_therapist_scoped_role(roles: set[str]) -> bool:
    return bool(roles & {"therapist", "assessment_session", "assessment_viewer_session"})


def _user_region_ids(current_user: User) -> list[int]:
    region_ids = getattr(current_user, "region_ids", None)
    if region_ids is None:
        return []
    if isinstance(region_ids, int):
        return [region_ids]
    return list(region_ids)


def _rbac_allowed(db: Session, user_id: int, code: str, action: str = "view") -> bool:
    column = {
        "view": "can_view",
        "create": "can_create",
        "edit": "can_edit",
        "delete": "can_delete",
    }.get(action, "can_view")
    try:
        row = db.execute(text(f"""
            SELECT MAX(permission.{column})
            FROM rbac_resources resource
            JOIN rbac_role_permissions permission ON permission.resource_id = resource.id
            JOIN user_roles user_role ON user_role.role_id = permission.role_id
            WHERE user_role.user_id = :user_id
              AND user_role.deleted_at IS NULL
              AND resource.code = :code
              AND resource.is_active = 1
        """), {"user_id": user_id, "code": code}).first()
        return bool(row and row[0])
    except Exception:
        return False


async def _require_calendar_write(current_user: User, db: Session) -> None:
    roles = _normalized_roles(await get_user_roles(current_user, db))
    if _rbac_allowed(db, current_user.id, "appointment.action.create", "create") or _is_front_office_role(roles):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only front office/admin can change appointments",
    )


async def _require_waitlist_access(current_user: User, db: Session) -> None:
    roles = _normalized_roles(await get_user_roles(current_user, db))
    if _rbac_allowed(db, current_user.id, "appointment.waitlist", "view") or _is_front_office_role(roles):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only front office/admin can view waitlist",
    )


def _therapist_ids_for_user(db: Session, current_user: User) -> list[int]:
    full_name = " ".join(part for part in [current_user.first_name, current_user.last_name] if part).strip()
    query = db.query(Therapist).filter(Therapist.is_active.is_(True))
    matches = query.filter(Therapist.user_id == current_user.id).all()
    if not matches and full_name:
        matches = query.filter(Therapist.name == full_name).all()
    return [therapist.id for therapist in matches]


def _filter_calendar_to_therapists(response: dict, therapist_ids: list[int]) -> dict:
    allowed = set(therapist_ids)
    response["therapists"] = [
        therapist
        for therapist in response.get("therapists", [])
        if int(therapist.get("therapist_id") or 0) in allowed
    ]
    response["total"] = len(response["therapists"])
    return response

@router.get(
    "/slots",
    response_model=List[SlotMasterResponse],
    status_code=status.HTTP_200_OK
)
async def get_all_slots(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    try:
        slots = SlotMasterService.get_all_slots(db)

        return slots

    except Exception as e:

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get(
    "/programs",
    status_code=status.HTTP_200_OK
)
async def get_programs(
    region_id: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetch active appointment programs from the programs table for the current user's regions."""
    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=_user_region_ids(current_user)
    )

    try:
        AppointmentService._ensure_program_schema(db, force_seed=True)

        region_ids = [region_id] if region_id else _user_region_ids(current_user)
        if isinstance(region_ids, int):
            region_ids = [region_ids]

        query = db.query(Program).filter(
            Program.is_active.is_(True),
            Program.deleted_at.is_(None),
        )
        if region_ids:
            query = query.filter(Program.region_id.in_(region_ids))

        programs = query.order_by(Program.program_name.asc(), Program.id.asc()).all()
        segment_rows = (
            db.query(ProgramSegment)
            .filter(
                ProgramSegment.program_id.in_([program.id for program in programs]),
                ProgramSegment.is_active.is_(True),
            )
            .order_by(ProgramSegment.program_id.asc(), ProgramSegment.sequence_no.asc())
            .all()
            if programs
            else []
        )
        segments_by_program = {}
        for segment in segment_rows:
            segments_by_program.setdefault(segment.program_id, []).append({
                "id": segment.id,
                "sequence_no": segment.sequence_no,
                "label": segment.label,
                "duration_minutes": segment.duration_minutes,
            })

        return {
            "total": len(programs),
            "data": [
                {
                    "id": program.id,
                    "region_id": program.region_id,
                    "program_name": program.program_name,
                    "per_session_amount": float(program.per_session_amount or 0),
                    "duration_minutes": int(program.duration_minutes or 45),
                    "capacity": int(program.capacity or 1),
                    "session_type": program.session_type or "individual",
                    "segments": segments_by_program.get(program.id, []),
                }
                for program in programs
            ],
        }

    except Exception as e:
        logger.error(
            f"Error fetching appointment programs: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching appointment programs"
        )

@router.get(
    "/waitlist-patients",
    status_code=status.HTTP_200_OK
)
async def get_waitlist_patients(
    region_id: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Fetch available patients based on logged in user's region access
    """

    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=_user_region_ids(current_user)
    )
    await _require_waitlist_access(current_user, db)

    try:
        
        response = AppointmentService.get_waitlist_patients(
            db=db,
            current_user=current_user,
            region_id=region_id,
        )

        logger.info(
            "Waitlist patients fetched successfully",
            extra={
                "user_id": current_user.id,
                "total_records": response["total"]
            }
        )

        return response

    except ValueError as e:

        logger.warning(
            f"Error fetching waitlist patients: {str(e)}",
            extra={
                "user_id": current_user.id
            }
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:

        logger.error(
            f"Error fetching waitlist patients: {str(e)}",
            extra={
                "user_id": current_user.id
            }
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching waitlist patients"
        )

@router.get(
    "/patient-plans/{patient_id}",
    status_code=status.HTTP_200_OK
)
async def get_patient_plans(
    patient_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Fetch session plans for dropdown based on patient id
    """

    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=_user_region_ids(current_user)
    )

    try:

        response = AppointmentService.get_patient_plans(
            db=db,
            patient_id=patient_id,
            current_user=current_user
        )

        logger.info(
            "Patient session plans fetched successfully",
            extra={
                "user_id": current_user.id,
                "patient_id": patient_id,
                "total_records": response["total"]
            }
        )

        return response

    except ValueError as e:

        logger.warning(
            f"Error fetching patient session plans: {str(e)}",
            extra={
                "user_id": current_user.id,
                "patient_id": patient_id
            }
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:

        logger.error(
            f"Error fetching patient session plans: {str(e)}",
            extra={
                "user_id": current_user.id,
                "patient_id": patient_id
            }
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching patient session plans"
        )
        
@router.get(
    "/patient-plans-therapies/{patient_session_plan_id}",
    status_code=status.HTTP_200_OK
)
async def get_patient_plans_therapies(
    patient_session_plan_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Fetch session plans for dropdown based on patient id
    """

    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=_user_region_ids(current_user)
    )

    try:

        response = AppointmentService.get_patient_plans_therapies(
            db=db,
            patient_session_plan_id=patient_session_plan_id,
            current_user=current_user
        )

        logger.info(
            "Patient session plans fetched successfully",
            extra={
                "user_id": current_user.id,
                "patient_session_plan_id": patient_session_plan_id,
                "total_records": response["total"]
            }
        )

        return response

    except ValueError as e:

        logger.warning(
            f"Error fetching patient session plans: {str(e)}",
            extra={
                "user_id": current_user.id,
                "patient_session_plan_id": patient_session_plan_id
            }
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:

        logger.error(
            f"Error fetching patient session plans: {str(e)}",
            extra={
                "user_id": current_user.id,
                "patient_session_plan_id": patient_session_plan_id
            }
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching patient session plans"
        )
        

@router.post(
    "/slot-booking",
    response_model=SlotBookingResponse,
    status_code=status.HTTP_201_CREATED
)
async def book_slot(
    booking_create: SlotBookingCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Book a slot after checking therapist availability, slot conflicts, and patient sessions."""
    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=_user_region_ids(current_user)
    )
    await _require_calendar_write(current_user, db)

    try:
        patient_ids = []
        requested_patient_ids = booking_create.patient_ids or ([booking_create.patient_id] if booking_create.patient_id else [])
        for patient_id in requested_patient_ids:
            if patient_id not in patient_ids:
                patient_ids.append(patient_id)
        if not patient_ids:
            raise ValueError("Select at least one child to book")

        allocations = booking_create.allocations or [{
            "therapist_id": booking_create.therapist_id,
            "therapy_id": booking_create.therapy_id,
            "slot_id": booking_create.slot_id,
            "duration_minutes": booking_create.duration_minutes,
            "is_primary": True,
        }]
        bulk_booking = len(patient_ids) > 1 or len(allocations) > 1

        bookings = []
        crt_parent_by_patient: dict[int, int] = {}
        for allocation in allocations:
            for patient_id in patient_ids:
                patient_booking_create = booking_create.copy(update={
                    "patient_id": patient_id,
                    "patient_ids": None,
                    "allocations": None,
                    "therapist_id": int(allocation.get("therapist_id") or booking_create.therapist_id),
                    "therapy_id": int(allocation.get("therapy_id") or booking_create.therapy_id),
                    "slot_id": int(allocation.get("slot_id") or booking_create.slot_id),
                    "duration_minutes": allocation.get("duration_minutes") or booking_create.duration_minutes,
                    "is_primary": bool(allocation.get("is_primary", True)),
                    "patient_session_plan_id": (
                        booking_create.patient_session_plan_id
                        if allocation.get("is_primary", True) and len(patient_ids) == 1
                        else None
                    ),
                    "patient_package_id": (
                        booking_create.patient_package_id
                        if allocation.get("is_primary", True) and len(patient_ids) == 1
                        else None
                    ),
                    "use_package": bool(booking_create.use_package and allocation.get("is_primary", True)),
                    "crt_program_booking_id": crt_parent_by_patient.get(patient_id),
                    "allow_shared_slot": bulk_booking,
                })
                booking = AppointmentService.book_slot(db, patient_booking_create, commit=not bulk_booking)
                patient_slot_booking = booking["patient_slot_booking"]
                if patient_slot_booking.crt_program_booking_id:
                    crt_parent_by_patient[patient_id] = patient_slot_booking.crt_program_booking_id
                therapist_slot_mapping = booking["therapist_slot_mapping"]
                plan_item = booking["patient_session_plan_item"]
                remaining_sessions = (
                    plan_item.allocated_sessions
                    - (plan_item.assigned_sessions or 0)
                    - (plan_item.completed_sessions or 0)
                ) if plan_item else None
                bookings.append({
                    "patient_slot_booking": patient_slot_booking,
                    "therapist_slot_mapping": therapist_slot_mapping,
                    "patient_session_plan_item": plan_item,
                    "remaining_sessions": remaining_sessions,
                })
        if bulk_booking:
            db.commit()
            for item in bookings:
                db.refresh(item["patient_slot_booking"])
                db.refresh(item["therapist_slot_mapping"])
                if item["patient_session_plan_item"]:
                    db.refresh(item["patient_session_plan_item"])

        booking = bookings[0]
        patient_slot_booking = booking["patient_slot_booking"]
        therapist_slot_mapping = booking["therapist_slot_mapping"]
        plan_item = booking["patient_session_plan_item"]
        remaining_sessions = booking["remaining_sessions"]
        logger.info(
            "Slot booked successfully",
            extra={
                "patient_slot_booking_id": patient_slot_booking.id,
                "therapist_slot_mapping_id": therapist_slot_mapping.id,
                "user_id": current_user.id,
                "patient_id": booking_create.patient_id,
                "patient_ids": patient_ids,
                "therapist_id": booking_create.therapist_id
            }
        )
        return {
            "success": True,
            "message": "Slot booked successfully",
            "patient_slot_booking_id": patient_slot_booking.id,
            "therapist_slot_mapping_id": therapist_slot_mapping.id,
            "appointment_id": None,
            "patient_session_plan_item_id": plan_item.id if plan_item else None,
            "allocated_sessions": plan_item.allocated_sessions if plan_item else None,
            "assigned_sessions": plan_item.assigned_sessions if plan_item else None,
            "completed_sessions": plan_item.completed_sessions if plan_item else None,
            "remaining_sessions": remaining_sessions,
            "patient_package_id": patient_slot_booking.patient_package_id,
            "is_package_session": bool(patient_slot_booking.is_package_session),
            "amount": float(patient_slot_booking.amount or 0),
            "paid_amount": float(patient_slot_booking.paid_amount or 0),
            "due_amount": float(patient_slot_booking.due_amount or 0),
            "payment_status": patient_slot_booking.payment_status,
            "booked_slots": [
                {
                    "patient_id": item["patient_slot_booking"].patient_id,
                    "patient_slot_booking_id": item["patient_slot_booking"].id,
                    "therapist_slot_mapping_id": item["therapist_slot_mapping"].id,
                    "patient_package_id": item["patient_slot_booking"].patient_package_id,
                    "is_package_session": bool(item["patient_slot_booking"].is_package_session),
                    "amount": float(item["patient_slot_booking"].amount or 0),
                    "paid_amount": float(item["patient_slot_booking"].paid_amount or 0),
                    "due_amount": float(item["patient_slot_booking"].due_amount or 0),
                    "payment_status": item["patient_slot_booking"].payment_status,
                }
                for item in bookings
            ],
        }

    except ValueError as e:
        db.rollback()
        logger.warning(
            f"Error booking slot: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:
        db.rollback()
        logger.error(
            f"Error booking slot: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.patch(
    "/slot-booking/{patient_slot_booking_id}",
    response_model=SlotBookingResponse,
    status_code=status.HTTP_200_OK
)
async def reschedule_slot(
    patient_slot_booking_id: int,
    booking_create: SlotBookingCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Edit a booked slot without changing the plan session count."""
    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=_user_region_ids(current_user)
    )
    await _require_calendar_write(current_user, db)

    try:
        booking = AppointmentService.reschedule_slot(db, patient_slot_booking_id, booking_create)
        patient_slot_booking = booking["patient_slot_booking"]
        therapist_slot_mapping = booking["therapist_slot_mapping"]
        plan_item = booking["patient_session_plan_item"]
        remaining_sessions = (
            plan_item.allocated_sessions
            - (plan_item.assigned_sessions or 0)
            - (plan_item.completed_sessions or 0)
        ) if plan_item else None

        return {
            "success": True,
            "message": "Slot updated successfully",
            "patient_slot_booking_id": patient_slot_booking.id,
            "therapist_slot_mapping_id": therapist_slot_mapping.id,
            "appointment_id": None,
            "patient_session_plan_item_id": plan_item.id if plan_item else None,
            "allocated_sessions": plan_item.allocated_sessions if plan_item else None,
            "assigned_sessions": plan_item.assigned_sessions if plan_item else None,
            "completed_sessions": plan_item.completed_sessions if plan_item else None,
            "remaining_sessions": remaining_sessions
        }

    except ValueError as e:
        logger.warning(
            f"Error updating slot: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:
        logger.error(
            f"Error updating slot: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating slot"
        )


@router.patch(
    "/slot-status/{patient_slot_booking_id}",
    response_model=SlotStatusActionResponse,
    status_code=status.HTTP_200_OK
)
async def update_slot_status(
    patient_slot_booking_id: int,
    payload: SlotStatusActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=_user_region_ids(current_user)
    )
    await _require_calendar_write(current_user, db)

    try:
        response = AppointmentService.update_slot_status(
            db,
            patient_slot_booking_id,
            payload.action,
            payload.cancel_type,
        )
        return response
    except ValueError as e:
        logger.warning(
            f"Error updating slot status: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(
            f"Error updating slot status: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating slot status"
        )


@router.patch(
    "/slot-cancel/{patient_slot_booking_id}",
    response_model=SlotCancelResponse,
    status_code=status.HTTP_200_OK
)
async def cancel_slot(
    patient_slot_booking_id: int,
    # cancel_request: SlotCancelRequest = SlotCancelRequest(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Cancel a booked slot and return the assigned session to the plan item."""
    # if cancel_request.region_id is not None:
    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=_user_region_ids(current_user)
    )
    await _require_calendar_write(current_user, db)

    try:
        cancellation = AppointmentService.cancel_slot(db, patient_slot_booking_id)
        patient_slot_booking = cancellation["patient_slot_booking"]
        therapist_slot_mapping = cancellation["therapist_slot_mapping"]
        plan_item = cancellation["patient_session_plan_item"]

        remaining_sessions = None
        if plan_item:
            remaining_sessions = (
                plan_item.allocated_sessions
                - (plan_item.assigned_sessions or 0)
                - (plan_item.completed_sessions or 0)
            )

        logger.info(
            "Slot cancelled successfully",
            extra={
                "patient_slot_booking_id": patient_slot_booking.id,
                "therapist_slot_mapping_id": therapist_slot_mapping.id if therapist_slot_mapping else None,
                "user_id": current_user.id
            }
        )

        return {
            "success": True,
            "message": "Slot cancelled successfully",
            "patient_slot_booking_id": patient_slot_booking.id,
            "therapist_slot_mapping_id": therapist_slot_mapping.id if therapist_slot_mapping else None,
            "patient_session_plan_item_id": plan_item.id if plan_item else None,
            "allocated_sessions": plan_item.allocated_sessions if plan_item else None,
            "assigned_sessions": plan_item.assigned_sessions if plan_item else None,
            "completed_sessions": plan_item.completed_sessions if plan_item else None,
            "remaining_sessions": remaining_sessions
        }

    except ValueError as e:
        logger.warning(
            f"Error cancelling slot: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:
        logger.error(
            f"Error cancelling slot: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error cancelling slot"
        )        

@router.get("/calendar")
async def get_appointment_calendar(
    selected_date: date = Query(None),
    start_date: date = Query(None),
    end_date: date = Query(None),
    region_id: int = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Fetch appointment calendar data for a selected date or date range.
    """

    # Check region access
    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=_user_region_ids(current_user)
    )
    roles = _normalized_roles(await get_user_roles(current_user, db))

    try:
        if start_date or end_date:
            if not start_date or not end_date:
                raise ValueError("Both start_date and end_date are required for calendar range")
            if start_date > end_date:
                raise ValueError("start_date cannot be after end_date")

            response = AppointmentService.get_calendar_range_view(
                db=db,
                start_date=start_date,
                end_date=end_date,
                region_ids=[region_id] if region_id else _user_region_ids(current_user)
            )
        else:
            if not selected_date:
                raise ValueError("selected_date is required")

            response = AppointmentService.get_calendar_view(
                db=db,
                selected_date=selected_date,
                region_ids=[region_id] if region_id else _user_region_ids(current_user)
            )

        if _is_therapist_scoped_role(roles) and not _is_front_office_role(roles):
            response = _filter_calendar_to_therapists(response, _therapist_ids_for_user(db, current_user))

        logger.info(
            "Appointment calendar fetched successfully",
            extra={
                "user_id": current_user.id,
                "selected_date": str(selected_date),
                "start_date": str(start_date) if start_date else None,
                "end_date": str(end_date) if end_date else None,
                "total_records": response.get("total", 0)
            }
        )

        return response

    except ValueError as e:
        logger.warning(
            f"Validation error while fetching appointment calendar: {str(e)}",
            extra={
                "user_id": current_user.id,
                "selected_date": str(selected_date),
                "start_date": str(start_date) if start_date else None,
                "end_date": str(end_date) if end_date else None,
            }
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:
        logger.error(
            f"Unexpected error while fetching appointment calendar: {str(e)}",
            extra={
                "user_id": current_user.id,
                "selected_date": str(selected_date),
                "start_date": str(start_date) if start_date else None,
                "end_date": str(end_date) if end_date else None,
            }
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch appointment calendar: {str(e)}"
        )

@router.get(
    "/therapists-list/{therapy_id}",
    status_code=status.HTTP_200_OK
)
async def get_therapists(
    therapy_id: int,
    region_id: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Fetch therapist dropdown based on therapy id
    """

    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=_user_region_ids(current_user)
    )

    try:

        response = AppointmentService.get_therapists(
            db=db,
            therapy_id=therapy_id,
            current_user=current_user,
            region_id=region_id,
        )

        logger.info(
            "Therapists fetched successfully",
            extra={
                "user_id": current_user.id,
                "therapy_id": therapy_id,
                "total_records": response["total"]
            }
        )

        return response

    except ValueError as e:

        logger.warning(
            f"Error fetching therapists: {str(e)}",
            extra={
                "user_id": current_user.id,
                "therapy_id": therapy_id
            }
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:

        logger.error(
            f"Error fetching therapists: {str(e)}",
            extra={
                "user_id": current_user.id,
                "therapy_id": therapy_id
            }
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching therapists"
        )
