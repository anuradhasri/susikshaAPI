from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from datetime import date
from app.core.database import get_db
from app.dependencies.auth import get_current_user, check_region_access
from app.schemas.schemas import (
    SlotBookingCreate,
    SlotBookingResponse, SlotCancelResponse, SlotMasterResponse,
    SlotStatusActionRequest, SlotStatusActionResponse
)
from app.services.appointment_service import AppointmentService, SlotMasterService
from app.models.models import User
from app.utils.logger import setup_logging

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])
logger = setup_logging(__name__)

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
    "/waitlist-patients",
    status_code=status.HTTP_200_OK
)
async def get_waitlist_patients(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Fetch available patients based on logged in user's region access
    """

    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=current_user.region_ids
    )

    try:
        
        response = AppointmentService.get_waitlist_patients(
            db=db,
            current_user=current_user
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
        target_region_id=current_user.region_ids
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
        target_region_id=current_user.region_ids
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
        target_region_id=current_user.region_ids
    )

    try:
        booking = AppointmentService.book_slot(db, booking_create)
        patient_slot_booking = booking["patient_slot_booking"]
        therapist_slot_mapping = booking["therapist_slot_mapping"]
        plan_item = booking["patient_session_plan_item"]
        remaining_sessions = (
            plan_item.allocated_sessions
            - (plan_item.assigned_sessions or 0)
            - (plan_item.completed_sessions or 0)
        ) if plan_item else None
        logger.info(
            "Slot booked successfully",
            extra={
                "patient_slot_booking_id": patient_slot_booking.id,
                "therapist_slot_mapping_id": therapist_slot_mapping.id,
                "user_id": current_user.id,
                "patient_id": booking_create.patient_id,
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
        }

    except ValueError as e:
        logger.warning(
            f"Error booking slot: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except Exception as e:
        logger.error(
            f"Error booking slot: {str(e)}",
            extra={"user_id": current_user.id}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error booking slot"
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
        target_region_id=current_user.region_ids
    )

    try:
        booking = AppointmentService.reschedule_slot(db, patient_slot_booking_id, booking_create)
        patient_slot_booking = booking["patient_slot_booking"]
        therapist_slot_mapping = booking["therapist_slot_mapping"]
        plan_item = booking["patient_session_plan_item"]
        remaining_sessions = (
            plan_item.allocated_sessions
            - (plan_item.assigned_sessions or 0)
            - (plan_item.completed_sessions or 0)
        )

        return {
            "success": True,
            "message": "Slot updated successfully",
            "patient_slot_booking_id": patient_slot_booking.id,
            "therapist_slot_mapping_id": therapist_slot_mapping.id,
            "appointment_id": None,
            "patient_session_plan_item_id": plan_item.id,
            "allocated_sessions": plan_item.allocated_sessions,
            "assigned_sessions": plan_item.assigned_sessions,
            "completed_sessions": plan_item.completed_sessions,
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
        target_region_id=current_user.region_ids
    )

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
        target_region_id=current_user.region_ids
    )

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
        target_region_id=current_user.region_ids
    )

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
                region_ids=current_user.region_ids
            )
        else:
            if not selected_date:
                raise ValueError("selected_date is required")

            response = AppointmentService.get_calendar_view(
                db=db,
                selected_date=selected_date,
                region_ids=current_user.region_ids
            )

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
            detail="Failed to fetch appointment calendar"
        )

@router.get(
    "/therapists-list/{therapy_id}",
    status_code=status.HTTP_200_OK
)
async def get_therapists(
    therapy_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Fetch therapist dropdown based on therapy id
    """

    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=current_user.region_ids
    )

    try:

        response = AppointmentService.get_therapists(
            db=db,
            therapy_id=therapy_id,
            current_user=current_user
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
