from collections import defaultdict

from fastapi import HTTPException
from grpc import Status
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime, date
from app.models.models import Appointment, Session as DBSession, SessionNote, SlotMaster, Therapist, PatientPackage, STATUS_ID_TO_CODE
from app.repositories.appointment_repository import AppointmentRepository
from app.schemas.schemas import AppointmentCreate, AppointmentUpdate, SessionCreate, SessionUpdate, SlotBookingCreate
from app.utils.query_utils import soft_delete, filter_by_region
from app.services.patient_service import PatientService
from app.repositories.user_repository import UserRepository


class AppointmentService:
    """Service for appointment operations"""
    @staticmethod
    def get_waitlist_patients(
        db: Session,
        current_user
    ):
        # =========================
        # FETCH AVAILABLE PATIENTS
        # =========================

        patients = UserRepository.get_available_patients(
            db=db,
            current_user = current_user
        )

        response = []

        for patient in patients:

            response.append({
                "id": patient.id,
                "first_name": patient.first_name,
                "last_name": patient.last_name,
                "full_name": f"{patient.first_name} {patient.last_name}",
                "phone": patient.phone,
                "email": patient.email,
                "diagnosis": patient.diagnosis,
                "region_id": patient.region_id,
                "is_available": patient.is_available
            })

        return {
            "success": True,
            "total": len(response),
            "data": response
        }    
    
    @staticmethod
    def get_patient_plans(
        db: Session,
        patient_id: int,
        current_user
    ):

        return AppointmentRepository.get_patient_plans(
            db=db,
            patient_id=patient_id
        )
    
    @staticmethod
    def get_patient_plans_therapies(
        db: Session,
        patient_session_plan_id: int,
        current_user
    ):

        return AppointmentRepository.get_patient_plans_therapies(
            db=db,
            patient_session_plan_id=patient_session_plan_id
        )
        
    @staticmethod
    def get_therapists(
        db: Session,
        therapy_id: int,
        current_user
    ):

        return AppointmentRepository.get_therapists(
            db=db,
            therapy_id=therapy_id,
            region_ids=current_user.region_ids
        )    
            
    @staticmethod
    def create_appointment(db: Session, appointment_create: AppointmentCreate) -> Appointment:
        """Create a new appointment"""
        # Check therapist availability
        therapist = AppointmentRepository.get_therapist(db, appointment_create.therapist_id)
        
        if not therapist or not therapist.is_available:
            raise ValueError("Therapist is not available")
        
        # Check patient has available sessions
        package_check = PatientService.check_package_availability(db, appointment_create.patient_id)
        if not package_check["has_available_sessions"]:
            raise ValueError("Patient has no available sessions in any package")
        
        if AppointmentRepository.has_conflict(
            db,
            appointment_create.therapist_id,
            appointment_create.start_time,
            appointment_create.end_time,
        ):
            raise ValueError("Therapist already has an appointment in this time slot")

        db_appointment = AppointmentRepository.create(db, appointment_create)
        db.commit()
        db.refresh(db_appointment)
        return db_appointment

    @staticmethod
    def book_slot(db: Session, booking_create: SlotBookingCreate):
        # """Book a patient slot using therapist leave, slot mapping, and plan item balance."""
        # therapist = AppointmentRepository.get_therapist(db, booking_create.therapist_id)
        # if not therapist or not therapist.is_active:
        #     raise ValueError("Therapist is not available")

        # if not AppointmentRepository.therapist_supports_therapy(
        #     db,
        #     booking_create.therapist_id,
        #     booking_create.therapy_id,
        # ):
        #     raise ValueError("Therapist is not available for selected therapy")

        slot = AppointmentRepository.get_slot(db, booking_create.slot_id)
        if not slot:
            raise ValueError("Selected slot is not available")

        therapist_leave = AppointmentRepository.get_therapist_leave(
            db,
            booking_create.therapist_id,
            booking_create.slot_date,
        )
        if AppointmentService._leave_blocks_slot(therapist_leave, slot.start_time):
            raise ValueError("Therapist is not available")

        if AppointmentRepository.get_active_therapist_slot_mapping(
            db,
            booking_create.therapist_id,
            booking_create.slot_id,
            booking_create.slot_date,
            booking_create.therapy_id,
        ):
            raise ValueError("Slot already booked for selected therapist and slot")

        if AppointmentRepository.patient_has_slot_booking(
            db,
            booking_create.patient_id,
            booking_create.slot_id,
            booking_create.slot_date,
            booking_create.therapy_id,
        ):
            raise ValueError("Patient already booked for selected therapy and slot")

        plan_item = AppointmentRepository.get_plan_item_for_booking(
            db,
            booking_create.patient_id,
            booking_create.patient_session_plan_id,
            booking_create.therapy_id,
        )
        if not plan_item:
            raise ValueError("No session left for selected therapy")

        assigned_sessions = plan_item.assigned_sessions or 0
        completed_sessions = plan_item.completed_sessions or 0
        used_sessions = assigned_sessions + completed_sessions
        if used_sessions >= plan_item.allocated_sessions:
            raise ValueError("No session left for selected therapy")

        therapist_slot_mapping = AppointmentRepository.create_therapist_slot_mapping(
            db,
            therapist_id=booking_create.therapist_id,
            slot_id=booking_create.slot_id,
            slot_date=booking_create.slot_date,
            therapy_id=booking_create.therapy_id,
        )

        patient_slot_booking = AppointmentRepository.create_patient_slot_booking(
            db,
            therapist_slot_mapping_id=therapist_slot_mapping.id,
            patient_session_plan_item_id=plan_item.id,
        )

        appointment = Appointment(
            patient_id=booking_create.patient_id,
            therapist_id=booking_create.therapist_id,
            start_time=datetime.combine(booking_create.slot_date, slot.start_time),
            end_time=datetime.combine(booking_create.slot_date, slot.end_time),
            status="scheduled",
            notes=booking_create.notes,
            region_id=booking_create.region_id,
        )
        db.add(appointment)

        plan_item.assigned_sessions = assigned_sessions + 1
        db.commit()
        db.refresh(patient_slot_booking)
        db.refresh(therapist_slot_mapping)
        db.refresh(plan_item)
        db.refresh(appointment)

        return {
            "patient_slot_booking": patient_slot_booking,
            "therapist_slot_mapping": therapist_slot_mapping,
            "patient_session_plan_item": plan_item,
            "appointment": appointment,
        }

    @staticmethod
    def cancel_slot(db: Session, patient_slot_booking_id: int):
        patient_slot_booking = AppointmentRepository.get_patient_slot_booking_for_update(
            db,
            patient_slot_booking_id,
        )
        if not patient_slot_booking:
            raise ValueError("Slot booking not found")

        if patient_slot_booking.status == "CANCELLED":
            raise ValueError("Slot booking already cancelled")

        if patient_slot_booking.status == "COMPLETED":
            raise ValueError("Completed slot cannot be cancelled")

        therapist_slot_mapping = patient_slot_booking.therapist_slot_mapping
        plan_item = patient_slot_booking.patient_session_plan_item

        patient_slot_booking.status = "CANCELLED"

        if therapist_slot_mapping and not AppointmentRepository.has_active_patient_booking_for_mapping(
            db,
            therapist_slot_mapping.id,
            exclude_patient_slot_booking_id=patient_slot_booking.id,
        ):
            therapist_slot_mapping.status = "CANCELLED"

        if plan_item:
            assigned_sessions = plan_item.assigned_sessions or 0
            if assigned_sessions > 0:
                plan_item.assigned_sessions = assigned_sessions - 1

        if therapist_slot_mapping and plan_item:
            slot = therapist_slot_mapping.slot
            AppointmentRepository.cancel_matching_appointment(
                db,
                patient_id=plan_item.patient_session_plan.patient_id,
                therapist_id=therapist_slot_mapping.therapist_id,
                start_time=datetime.combine(therapist_slot_mapping.slot_date, slot.start_time),
                end_time=datetime.combine(therapist_slot_mapping.slot_date, slot.end_time),
            )

        db.commit()
        db.refresh(patient_slot_booking)
        if therapist_slot_mapping:
            db.refresh(therapist_slot_mapping)
        if plan_item:
            db.refresh(plan_item)

        return {
            "patient_slot_booking": patient_slot_booking,
            "therapist_slot_mapping": therapist_slot_mapping,
            "patient_session_plan_item": plan_item,
        }

    @staticmethod
    def _leave_blocks_slot(therapist_leave, slot_start) -> bool:
        if not therapist_leave:
            return False

        leave_session = str(therapist_leave.leave_session or "").lower()
        if leave_session in {"full_day", "full day", "fullday", "full"}:
            return True

        if leave_session in {"morning", "first_half", "first half", "forenoon"}:
            return slot_start.hour < 13

        if leave_session in {"afternoon", "second_half", "second half", "half_day"}:
            return slot_start.hour >= 13

        return True
    
    @staticmethod
    def get_calendar_view(
        db,
        selected_date,
        region_ids
    ):

        records = AppointmentRepository.get_calendar_data(
            db=db,
            selected_date=selected_date,
            region_ids=region_ids
        )

        leaves = AppointmentRepository.get_therapist_leaves(
            db=db,
            selected_date=selected_date
        )

        leaves_by_therapist = {leave.therapist_id: leave for leave in leaves}

        therapist_map = defaultdict(lambda: {
            "therapist_id": None,
            "therapist_name": None,
            "therapy_name": None,
            "slots": []
        })

        for therapist in AppointmentRepository.get_active_therapists_for_calendar(db, region_ids):
            therapist_map[therapist.id]["therapist_id"] = therapist.id
            therapist_map[therapist.id]["therapist_name"] = therapist.name
            therapist_map[therapist.id]["therapy_name"] = None

        for row in records:

            therapist_id = row.therapist_id

            therapist_name = row.therapist_name
            leave = leaves_by_therapist.get(therapist_id)
            is_leave_slot = AppointmentService._leave_blocks_slot(leave, row.start_time)
            assigned_sessions = row.assigned_sessions or 0
            completed_sessions = row.completed_sessions or 0
            allocated_sessions = row.allocated_sessions or 0
            remaining_sessions = max(0, allocated_sessions - assigned_sessions - completed_sessions)

            therapist_map[therapist_id]["therapist_id"] = therapist_id
            therapist_map[therapist_id]["therapist_name"] = therapist_name
            therapist_map[therapist_id]["therapy_name"] = row.therapy_name

            patient_name = None

            if row.patient_first_name:
                patient_name = (
                    f"{row.patient_first_name} "
                    f"{row.patient_last_name}"
                )

            therapist_map[therapist_id]["slots"].append({
                "slot_mapping_id": row.slot_mapping_id,
                "patient_slot_booking_id": row.patient_slot_booking_id,
                "slot_id": row.slot_id,
                "slot_date": str(selected_date),
                "slot_time": row.start_time,
                "start_time": row.start_time,
                "end_time": row.end_time,
                "duration_minutes": row.duration_minutes,
                "therapy_id": row.therapy_id,
                "therapy_name": row.therapy_name,
                "patient_id": row.patient_id,
                "patient_name": patient_name,
                "patient_code": f"CP-{row.patient_id}" if row.patient_id else None,
                "patient_phone": row.patient_phone,
                "patient_session_plan_id": row.patient_session_plan_id,
                "patient_session_plan_item_id": row.patient_session_plan_item_id,
                "plan_name": row.plan_name,
                "session_no": assigned_sessions + completed_sessions,
                "total_sessions": row.plan_total_sessions,
                "allocated_sessions": allocated_sessions,
                "assigned_sessions": assigned_sessions,
                "completed_sessions": completed_sessions,
                "remaining_sessions": remaining_sessions,
                "amount_per_session": float(row.amount_per_session or 0),
                "package_warning": remaining_sessions <= 0,
                "leave_session": leave.leave_session if leave and is_leave_slot else None,
                "leave_reason": leave.reason if leave and is_leave_slot else None,
                "patient_slot_booking_status": STATUS_ID_TO_CODE["patient_slot_booking"].get(
                    row.patient_slot_booking_status_id
                ),
                "therapist_slot_mapping_status": STATUS_ID_TO_CODE["therapist_slot_mapping"].get(
                    row.therapist_slot_mapping_status_id
                ),
                "status": "Leave" if is_leave_slot else ("Booked" if patient_name else "Available"),
            })

        calendar_slots = db.query(SlotMaster).filter(SlotMaster.is_active == 1).all()
        for therapist in AppointmentRepository.get_active_therapists_for_calendar(db, region_ids):
            leave = leaves_by_therapist.get(therapist.id)
            if not leave:
                continue
            existing_leave_keys = {
                (slot["slot_id"], slot["status"])
                for slot in therapist_map[therapist.id]["slots"]
            }
            for slot in calendar_slots:
                if not AppointmentService._leave_blocks_slot(leave, slot.start_time):
                    continue
                if (slot.id, "Leave") in existing_leave_keys:
                    continue
                therapist_map[therapist.id]["slots"].append({
                    "slot_mapping_id": None,
                    "patient_slot_booking_id": None,
                    "slot_id": slot.id,
                    "slot_date": str(selected_date),
                    "slot_time": slot.start_time,
                    "start_time": slot.start_time,
                    "end_time": slot.end_time,
                    "duration_minutes": slot.duration_minutes,
                    "therapy_id": None,
                    "therapy_name": None,
                    "patient_id": None,
                    "patient_name": None,
                    "patient_code": None,
                    "patient_phone": None,
                    "patient_session_plan_id": None,
                    "patient_session_plan_item_id": None,
                    "plan_name": None,
                    "session_no": None,
                    "total_sessions": None,
                    "allocated_sessions": None,
                    "assigned_sessions": None,
                    "completed_sessions": None,
                    "remaining_sessions": None,
                    "amount_per_session": 0,
                    "package_warning": False,
                    "leave_session": leave.leave_session,
                    "leave_reason": leave.reason,
                    "patient_slot_booking_status": None,
                    "therapist_slot_mapping_status": None,
                    "status": "Leave",
                })

        for therapist in therapist_map.values():
            therapist["slots"].sort(key=lambda slot: slot["start_time"])

        return {
            "date": str(selected_date),
            "therapists": list(therapist_map.values())
        }    
    
    @staticmethod
    def get_appointment_by_id(db: Session, appointment_id: int, region_id: int = None) -> Appointment:
        """Get appointment by ID with region filtering"""
        return AppointmentRepository.get_by_id(db, appointment_id, region_id)
    
    @staticmethod
    def update_appointment(db: Session, appointment_id: int, appointment_update: AppointmentUpdate, region_id: int = None) -> Appointment:
        """Update appointment"""
        appointment = AppointmentService.get_appointment_by_id(db, appointment_id, region_id)
        if not appointment:
            return None
        
        AppointmentRepository.update(db, appointment, appointment_update)
        db.commit()
        db.refresh(appointment)
        return appointment
    
    @staticmethod
    def delete_appointment(db: Session, appointment_id: int) -> bool:
        """Soft delete appointment"""
        return AppointmentRepository.delete(db, appointment_id)
    
    @staticmethod
    def list_appointments(
        db: Session,
        region_id: int = None,
        region_ids: list[int] = None,
        therapist_id: int = None,
        patient_id: int = None,
        start_date: datetime = None,
        end_date: datetime = None,
        skip: int = 0,
        limit: int = 100
    ) -> tuple:
        """List appointments with filtering"""
        return AppointmentRepository.list(
            db,
            region_id=region_id,
            region_ids=region_ids,
            therapist_id=therapist_id,
            patient_id=patient_id,
            start_date=start_date,
            end_date=end_date,
            skip=skip,
            limit=limit,
        )
    
    @staticmethod
    def check_therapist_availability(db: Session, therapist_id: int, start_time: datetime, end_time: datetime) -> bool:
        """Check if therapist is available for given time slot"""
        return not AppointmentRepository.has_conflict(db, therapist_id, start_time, end_time)


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


class SlotMasterService:

    @staticmethod
    def get_all_slots(db: Session):

        slots = (
            db.query(SlotMaster)
            .filter(SlotMaster.is_active == 1)
            .order_by(SlotMaster.id.asc())
            .all()
        )

        return slots
    
    
