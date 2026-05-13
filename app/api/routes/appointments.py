from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from datetime import datetime
from app.core.database import get_db
from app.dependencies.auth import get_current_user, check_region_access, get_user_roles
from app.schemas.schemas import (
    AppointmentCreate, AppointmentUpdate, AppointmentResponse,
    AppointmentDetailResponse, PaginatedResponse, SlotMasterResponse
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
        target_region_id=current_user.region_id
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
        
@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    appointment_create: AppointmentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new appointment"""
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=appointment_create.region_id)
    
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
    appointment = AppointmentService.get_appointment_by_id(db, appointment_id, current_user.region_id)
    
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=appointment.region_id)
    
    return appointment


@router.patch("/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    appointment_id: int,
    appointment_update: AppointmentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update appointment"""
    appointment = AppointmentService.get_appointment_by_id(db, appointment_id, current_user.region_id)
    
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=appointment.region_id)
    
    try:
        updated_appointment = AppointmentService.update_appointment(
            db, appointment_id, appointment_update, current_user.region_id
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
    appointment = AppointmentService.get_appointment_by_id(db, appointment_id, current_user.region_id)
    
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=appointment.region_id)
    
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
            Therapist.user_id == current_user.id,
            Therapist.deleted_at.is_(None)
        ).first()
        
        if therapist:
            therapist_id = therapist.id
    
    appointments, total = AppointmentService.list_appointments(
        db,
        region_id=current_user.region_id,
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

