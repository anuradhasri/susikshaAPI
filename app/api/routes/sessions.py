from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from datetime import date
from app.core.database import get_db
from app.dependencies.auth import get_current_user, get_user_roles
from app.schemas.schemas import SessionResponse, SessionCreate, SessionUpdate, SessionNoteCreate, SessionNoteResponse, PaginatedResponse
from app.services.appointment_service import SessionService
from app.models.models import User
from app.utils.logger import setup_logging

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
logger = setup_logging(__name__)


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    session_create: SessionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new session"""
    try:
        session = SessionService.create_session(db, session_create)
        logger.info(
            f"Session created",
            extra={
                "session_id": session.id,
                "user_id": current_user.id,
                "patient_id": session.patient_id
            }
        )
        return session
    except Exception as e:
        logger.error(f"Error creating session: {str(e)}", extra={"user_id": current_user.id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating session"
        )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get session by ID"""
    session = SessionService.get_session_by_id(db, session_id)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    # Therapists can only see their own sessions
    roles = await get_user_roles(current_user, db)
    if "therapist" in roles and session.therapist_id:
        from app.models.models import Therapist
        therapist = db.query(Therapist).filter(
            Therapist.user_id == current_user.id,
            Therapist.deleted_at.is_(None)
        ).first()
        
        if not therapist or session.therapist_id != therapist.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have access to this session"
            )
    
    return session


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: int,
    session_update: SessionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update session"""
    session = SessionService.get_session_by_id(db, session_id)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    try:
        updated_session = SessionService.update_session(db, session_id, session_update)
        logger.info(
            f"Session updated",
            extra={"session_id": session_id, "user_id": current_user.id}
        )
        return updated_session
    except Exception as e:
        logger.error(f"Error updating session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating session"
        )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete session"""
    session = SessionService.get_session_by_id(db, session_id)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    try:
        SessionService.delete_session(db, session_id)
        logger.info(
            f"Session deleted",
            extra={"session_id": session_id, "user_id": current_user.id}
        )
    except Exception as e:
        logger.error(f"Error deleting session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error deleting session"
        )


@router.get("", response_model=PaginatedResponse)
async def list_sessions(
    patient_id: int = Query(None),
    therapist_id: int = Query(None),
    start_date: date = Query(None),
    end_date: date = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List sessions with filtering"""
    # Therapists can only see their own sessions
    roles = await get_user_roles(current_user, db)
    if "therapist" in roles:
        from app.models.models import Therapist
        therapist = db.query(Therapist).filter(
            Therapist.user_id == current_user.id,
            Therapist.deleted_at.is_(None)
        ).first()
        
        if therapist:
            therapist_id = therapist.id
    
    sessions, total = SessionService.list_sessions(
        db,
        patient_id=patient_id,
        therapist_id=therapist_id,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit
    )
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": sessions
    }


@router.post("/{session_id}/notes", response_model=SessionNoteResponse, status_code=status.HTTP_201_CREATED)
async def add_session_note(
    session_id: int,
    note_create: SessionNoteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add note to session"""
    session = SessionService.get_session_by_id(db, session_id)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    try:
        note = SessionService.add_session_note(
            db,
            session_id,
            note_create.note_type,
            note_create.content,
            current_user.id
        )
        logger.info(
            f"Session note added",
            extra={"session_id": session_id, "user_id": current_user.id}
        )
        return note
    except Exception as e:
        logger.error(f"Error adding session note: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error adding session note"
        )


@router.post("/{session_id}/complete", response_model=SessionResponse)
async def complete_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark session as completed"""
    session = SessionService.get_session_by_id(db, session_id)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    try:
        completed_session = SessionService.complete_session(db, session_id)
        logger.info(
            f"Session marked as completed",
            extra={"session_id": session_id, "user_id": current_user.id}
        )
        return completed_session
    except Exception as e:
        logger.error(f"Error completing session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error completing session"
        )
