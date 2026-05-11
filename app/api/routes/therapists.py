from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies.auth import get_current_user, check_region_access
from app.schemas.schemas import TherapistResponse, SessionResponse, PaginatedResponse
from app.services.billing_service import TherapistService
from app.models.models import User
from app.utils.logger import setup_logging

router = APIRouter(prefix="/api/v1/therapists", tags=["therapists"])
logger = setup_logging(__name__)


@router.get("/{therapist_id}", response_model=TherapistResponse)
async def get_therapist(
    therapist_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get therapist by ID"""
    therapist = TherapistService.get_therapist_by_id(db, therapist_id, current_user.region_id)
    
    if not therapist:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Therapist not found"
        )
    
    # Check region access
    await check_region_access(current_user, therapist.region_id)
    
    return therapist


@router.get("", response_model=PaginatedResponse)
async def list_therapists(
    is_available: bool = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List therapists"""
    therapists, total = TherapistService.list_therapists(
        db,
        region_id=current_user.region_id,
        is_available=is_available,
        skip=skip,
        limit=limit
    )
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": therapists
    }
