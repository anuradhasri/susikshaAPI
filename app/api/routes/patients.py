from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status, Query
from fastapi.params import File
from sqlalchemy.orm import Session
from streamlit import json
from app.core.database import get_db
from app.dependencies.auth import get_current_user, check_region_access, get_user_roles
from app.schemas.schemas import (
    PatientCreate, PatientUpdate, PatientResponse, PatientPackageResponse, PaginatedResponse
)
from app.services.patient_service import PatientService
from app.models.models import User
from app.utils.logger import setup_logging

router = APIRouter(prefix="/api/v1/patients", tags=["patients"])
logger = setup_logging(__name__)


# @router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
# async def create_patient(
#     patient_create: PatientCreate,
#     current_user: User = Depends(get_current_user),
#     db: Session = Depends(get_db)
# ):
#     """Create a new patient"""
#     # Check region access
#     await check_region_access(current_user=current_user, db=db, target_region_id=patient_create.region_id)
    
#     try:
#         patient = PatientService.create_patient(db, patient_create)
#         logger.info(
#             f"Patient created",
#             extra={
#                 "patient_id": patient.id,
#                 "user_id": current_user.id,
#                 "region_id": patient.region_id
#             }
#         )
#         return patient
#     except Exception as e:
#         logger.error(f"Error creating patient: {str(e)}", extra={"user_id": current_user.id})
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Error creating patient"
#         )
async def create_patient(
    patient_data: str = Form(...),
    files: Optional[List[UploadFile]] = File(None),
    document_types: Optional[List[str]] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        patient_dict = json.loads(patient_data)

        patient_create = PatientCreate(**patient_dict)

        await check_region_access(
            current_user=current_user,
            db=db,
            target_region_id=patient_create.region_id
        )

        patient = PatientService.create_patient(
            db=db,
            patient_create=patient_create,
            files=files,
            document_types=document_types,
            current_user=current_user
        )

        logger.info(
            "Patient created",
            extra={
                "patient_id": patient.id,
                "user_id": current_user.id,
                "region_id": patient.region_id
            }
        )

        return patient

    except Exception as e:
        logger.error(
            f"Error creating patient: {str(e)}",
            extra={"user_id": current_user.id}
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    patient_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get patient by ID"""
    patient = PatientService.get_patient_by_id(db, patient_id, current_user.region_id)
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=patient.region_id)
    
    return patient


@router.patch("/{patient_id}", response_model=PatientResponse)
async def update_patient(
    patient_id: int,
    patient_update: PatientUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update patient"""
    patient = PatientService.get_patient_by_id(db, patient_id, current_user.region_id)
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=patient.region_id)
    
    try:
        updated_patient = PatientService.update_patient(db, patient_id, patient_update, current_user.region_id)
        logger.info(
            f"Patient updated",
            extra={"patient_id": patient_id, "user_id": current_user.id}
        )
        return updated_patient
    except Exception as e:
        logger.error(f"Error updating patient: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating patient"
        )


@router.delete("/{patient_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_patient(
    patient_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete patient"""
    patient = PatientService.get_patient_by_id(db, patient_id, current_user.region_id)
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=patient.region_id)
    
    try:
        PatientService.delete_patient(db, patient_id)
        logger.info(
            f"Patient deleted",
            extra={"patient_id": patient_id, "user_id": current_user.id}
        )
    except Exception as e:
        logger.error(f"Error deleting patient: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error deleting patient"
        )


# @router.get("", response_model=PaginatedResponse)
# async def list_patients(
#     skip: int = Query(0, ge=0),
#     limit: int = Query(100, ge=1, le=1000),
#     current_user: User = Depends(get_current_user),
#     db: Session = Depends(get_db)
# ):
#     """List patients"""
#     patients, total = PatientService.list_patients(
#         db,
#         region_id=current_user.region_id,
#         skip=skip,
#         limit=limit
#     )
    
#     return {
#         "total": total,
#         "skip": skip,
#         "limit": limit,
#         "items": patients
#     }

@router.get("", response_model=PaginatedResponse)
async def list_patients(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    patients, total = PatientService.list_patients(
        db,
        region_ids=current_user.region_ids, 
        skip=skip,
        limit=limit
    )
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": patients
    }


@router.get("/{patient_id}/packages", response_model=list)
async def get_patient_packages(
    patient_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get patient packages"""
    patient = PatientService.get_patient_by_id(db, patient_id, current_user.region_id)
    
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=patient.region_id)
    
    packages = PatientService.get_patient_packages(db, patient_id, current_user.region_id)
    return packages
