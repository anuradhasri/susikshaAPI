from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime, date
from app.models.models import Appointment, Session as DBSession, SessionNote, Therapist, PatientPackage
from app.schemas.schemas import AppointmentCreate, AppointmentUpdate, SessionCreate, SessionUpdate
from app.utils.query_utils import soft_delete, filter_by_region
from app.services.patient_service import PatientService


class AppointmentService:
    """Service for appointment operations"""
    
    @staticmethod
    def create_appointment(db: Session, appointment_create: AppointmentCreate) -> Appointment:
        """Create a new appointment"""
        # Check therapist availability
        therapist = db.query(Therapist).filter(
            Therapist.id == appointment_create.therapist_id,
            Therapist.deleted_at.is_(None)
        ).first()
        
        if not therapist or not therapist.is_available:
            raise ValueError("Therapist is not available")
        
        # Check patient has available sessions
        package_check = PatientService.check_package_availability(db, appointment_create.patient_id)
        if not package_check["has_available_sessions"]:
            raise ValueError("Patient has no available sessions in any package")
        
        db_appointment = Appointment(**appointment_create.dict())
        db.add(db_appointment)
        db.commit()
        db.refresh(db_appointment)
        return db_appointment
    
    @staticmethod
    def get_appointment_by_id(db: Session, appointment_id: int, region_id: int = None) -> Appointment:
        """Get appointment by ID with region filtering"""
        query = db.query(Appointment).filter(
            Appointment.id == appointment_id,
            Appointment.deleted_at.is_(None)
        )
        
        if region_id:
            query = filter_by_region(query, region_id, Appointment)
        
        return query.first()
    
    @staticmethod
    def update_appointment(db: Session, appointment_id: int, appointment_update: AppointmentUpdate, region_id: int = None) -> Appointment:
        """Update appointment"""
        appointment = AppointmentService.get_appointment_by_id(db, appointment_id, region_id)
        if not appointment:
            return None
        
        for field, value in appointment_update.dict(exclude_unset=True).items():
            setattr(appointment, field, value)
        
        db.commit()
        db.refresh(appointment)
        return appointment
    
    @staticmethod
    def delete_appointment(db: Session, appointment_id: int) -> bool:
        """Soft delete appointment"""
        appointment = soft_delete(db, Appointment, appointment_id)
        return appointment is not None
    
    @staticmethod
    def list_appointments(
        db: Session,
        region_id: int = None,
        therapist_id: int = None,
        patient_id: int = None,
        start_date: datetime = None,
        end_date: datetime = None,
        skip: int = 0,
        limit: int = 100
    ) -> tuple:
        """List appointments with filtering"""
        query = db.query(Appointment).filter(Appointment.deleted_at.is_(None))
        
        if region_id:
            query = filter_by_region(query, region_id, Appointment)
        
        if therapist_id:
            query = query.filter(Appointment.therapist_id == therapist_id)
        
        if patient_id:
            query = query.filter(Appointment.patient_id == patient_id)
        
        if start_date:
            query = query.filter(Appointment.start_time >= start_date)
        
        if end_date:
            query = query.filter(Appointment.end_time <= end_date)
        
        total = query.count()
        appointments = query.offset(skip).limit(limit).all()
        
        return appointments, total
    
    @staticmethod
    def check_therapist_availability(db: Session, therapist_id: int, start_time: datetime, end_time: datetime) -> bool:
        """Check if therapist is available for given time slot"""
        conflict = db.query(Appointment).filter(
            Appointment.therapist_id == therapist_id,
            Appointment.status != "cancelled",
            or_(
                and_(Appointment.start_time < end_time, Appointment.end_time > start_time)
            ),
            Appointment.deleted_at.is_(None)
        ).first()
        
        return conflict is None


class SessionService:
    """Service for session operations"""
    
    @staticmethod
    def create_session(db: Session, session_create: SessionCreate) -> DBSession:
        """Create a new session"""
        db_session = DBSession(**session_create.dict())
        
        db.add(db_session)
        db.commit()
        db.refresh(db_session)
        return db_session
    
    @staticmethod
    def get_session_by_id(db: Session, session_id: int) -> DBSession:
        """Get session by ID"""
        return db.query(DBSession).filter(
            DBSession.id == session_id,
            DBSession.deleted_at.is_(None)
        ).first()
    
    @staticmethod
    def update_session(db: Session, session_id: int, session_update: SessionUpdate) -> DBSession:
        """Update session"""
        session = SessionService.get_session_by_id(db, session_id)
        if not session:
            return None
        
        for field, value in session_update.dict(exclude_unset=True).items():
            setattr(session, field, value)
        
        db.commit()
        db.refresh(session)
        return session
    
    @staticmethod
    def delete_session(db: Session, session_id: int) -> bool:
        """Soft delete session"""
        session = soft_delete(db, DBSession, session_id)
        return session is not None
    
    @staticmethod
    def list_sessions(
        db: Session,
        patient_id: int = None,
        therapist_id: int = None,
        start_date: date = None,
        end_date: date = None,
        skip: int = 0,
        limit: int = 100
    ) -> tuple:
        """List sessions with filtering"""
        query = db.query(DBSession).filter(DBSession.deleted_at.is_(None))
        
        if patient_id:
            query = query.filter(DBSession.patient_id == patient_id)
        
        if therapist_id:
            query = query.filter(DBSession.therapist_id == therapist_id)
        
        if start_date:
            query = query.filter(DBSession.session_date >= start_date)
        
        if end_date:
            query = query.filter(DBSession.session_date <= end_date)
        
        total = query.count()
        sessions = query.offset(skip).limit(limit).all()
        
        return sessions, total
    
    @staticmethod
    def add_session_note(db: Session, session_id: int, note_type: str, content: str, created_by: int) -> SessionNote:
        """Add note to session"""
        note = SessionNote(
            session_id=session_id,
            note_type=note_type,
            content=content,
            created_by=created_by
        )
        
        db.add(note)
        db.commit()
        db.refresh(note)
        return note
    
    @staticmethod
    def complete_session(db: Session, session_id: int) -> DBSession:
        """Mark session as completed"""
        session = SessionService.get_session_by_id(db, session_id)
        if not session:
            return None
        
        session.status = "completed"
        session.billing_status = "completed"
        
        # Update patient package
        if session.session_assignments:
            for assignment in session.session_assignments:
                patient_pkg = db.query(PatientPackage).filter(
                    PatientPackage.id == assignment.package_id
                ).first()
                
                if patient_pkg:
                    PatientService.update_package_sessions(db, patient_pkg.id, completed=True)
        
        db.commit()
        db.refresh(session)
        return session
