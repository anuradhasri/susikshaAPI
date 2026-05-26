from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from datetime import date, datetime
from app.core.database import get_db
from app.dependencies.auth import get_current_user, check_region_access, get_user_roles
from app.schemas.schemas import (
    AppointmentCreate, AppointmentUpdate, AppointmentResponse,
    AppointmentDetailResponse, PaginatedResponse, SlotBookingCreate,
    SlotBookingResponse, SlotCancelRequest, SlotCancelResponse, SlotMasterResponse
)
from app.services.appointment_service import AppointmentService, SlotMasterService
from app.models.models import User
from app.services.patient_service import PatientService
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
        """List appointments with filtering"""
        roles = await get_user_roles(current_user, db)
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
        appointment = booking["appointment"]
        remaining_sessions = (
            plan_item.allocated_sessions
            - (plan_item.assigned_sessions or 0)
            - (plan_item.completed_sessions or 0)
        )
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
            "appointment_id": appointment.id,
            "patient_session_plan_item_id": plan_item.id,
            "allocated_sessions": plan_item.allocated_sessions,
            "assigned_sessions": plan_item.assigned_sessions,
            "completed_sessions": plan_item.completed_sessions,
            "remaining_sessions": remaining_sessions
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
        appointment = booking["appointment"]
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
            "appointment_id": appointment.id if appointment else None,
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
    selected_date: date,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Fetch appointment calendar data for the selected date.
    """

    # Check region access
    await check_region_access(
        current_user=current_user,
        db=db,
        target_region_id=current_user.region_ids
    )

    try:
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
                "total_records": response.get("total", 0)
            }
        )

        return response

    except ValueError as e:
        logger.warning(
            f"Validation error while fetching appointment calendar: {str(e)}",
            extra={
                "user_id": current_user.id,
                "selected_date": str(selected_date)
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
                "selected_date": str(selected_date)
            }
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch appointment calendar"
        )



@router.get("", response_model=PaginatedResponse)
async def list_appointments(
    start: datetime = Query(None),
    end: datetime = Query(None),
    patient_id: int = Query(None),
    therapist_id: int = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List appointments with filtering"""
    roles = await get_user_roles(current_user, db)
    
    # Therapists can only see their own appointments
    if "therapist" in roles:
        from app.models.models import Therapist
        therapist = db.query(Therapist).filter(
            Therapist.name == f"{current_user.first_name} {current_user.last_name}",
            Therapist.is_active == 1
        ).first()
        
        if therapist:
            therapist_id = therapist.id
    
    appointments, total = AppointmentService.list_appointments(
        db,
        region_ids=current_user.region_ids,
        therapist_id=therapist_id,
        patient_id=patient_id,
        start_date=start,
        end_date=end,
        skip=skip,
        limit=limit
    )
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": appointments
    }

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
                
@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    appointment_create: AppointmentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new appointment"""
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=[appointment_create.region_id])
    
    try:
        appointment = AppointmentService.create_appointment(db, appointment_create)
        logger.info(
            f"Appointment created",
            extra={
                "appointment_id": appointment.id,
                "user_id": current_user.id,
                "patient_id": appointment.patient_id
            }
        )
        return appointment
    except ValueError as e:
        logger.warning(f"Error creating appointment: {str(e)}", extra={"user_id": current_user.id})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error creating appointment: {str(e)}", extra={"user_id": current_user.id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating appointment"
        )

@router.get("/{appointment_id}", response_model=AppointmentDetailResponse)
async def get_appointment(
    appointment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get appointment by ID"""
    appointment = AppointmentService.get_appointment_by_id(db, appointment_id)
    
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=[appointment.region_id])
    
    return appointment


@router.patch("/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    appointment_id: int,
    appointment_update: AppointmentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update appointment"""
    appointment = AppointmentService.get_appointment_by_id(db, appointment_id)
    
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=[appointment.region_id])
    
    try:
        updated_appointment = AppointmentService.update_appointment(
            db, appointment_id, appointment_update
        )
        logger.info(
            f"Appointment updated",
            extra={"appointment_id": appointment_id, "user_id": current_user.id}
        )
        return updated_appointment
    except Exception as e:
        logger.error(f"Error updating appointment: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating appointment"
        )


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_appointment(
    appointment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete appointment"""
    appointment = AppointmentService.get_appointment_by_id(db, appointment_id)
    
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=[appointment.region_id])
    
    try:
        AppointmentService.delete_appointment(db, appointment_id)
        logger.info(
            f"Appointment deleted",
            extra={"appointment_id": appointment_id, "user_id": current_user.id}
        )
    except Exception as e:
        logger.error(f"Error deleting appointment: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error deleting appointment"
        )


