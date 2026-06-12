from collections import defaultdict

from fastapi import HTTPException
from grpc import Status
from sqlalchemy.orm import Session
from sqlalchemy import and_, inspect, or_, text
from datetime import datetime, date, time, timedelta
from app.models.models import Appointment, Session as DBSession, SessionNote, SlotMaster, Therapist, Patient, PatientPackage, Program, ProgramSegment, STATUS_ID_TO_CODE, MASTER_LOOKUP_DATA, PatientSlotBooking, TherapistSlotMapping
from app.repositories.appointment_repository import AppointmentRepository
from app.schemas.schemas import AppointmentCreate, AppointmentUpdate, SessionCreate, SessionUpdate, SlotBookingCreate
from app.utils.query_utils import soft_delete, filter_by_region
from app.services.patient_service import PatientService
from app.repositories.user_repository import UserRepository


class AppointmentService:
    """Service for appointment operations"""
    BREAK_START_MINUTES = 13 * 60 + 30
    BREAK_END_MINUTES = 14 * 60
    PATIENT_SLOT_BOOKING_UNPAID_CANCELLED_ID = MASTER_LOOKUP_DATA["patient_slot_booking"]["UNPAID_CANCELLED"]
    PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID = MASTER_LOOKUP_DATA["patient_slot_booking"]["PAID_CANCELLED"]
    PATIENT_SLOT_BOOKING_COMPLETED_ID = MASTER_LOOKUP_DATA["patient_slot_booking"]["COMPLETED"]
    THERAPIST_SLOT_MAPPING_CANCELLED_ID = MASTER_LOOKUP_DATA["therapist_slot_mapping"]["CANCELLED"]
    THERAPIST_SLOT_MAPPING_COMPLETED_ID = MASTER_LOOKUP_DATA["therapist_slot_mapping"]["COMPLETED"]
    _PACKAGE_BILLING_SCHEMA_READY = False
    _PROGRAM_SCHEMA_READY = False

    @staticmethod
    def _ensure_package_billing_schema(db: Session) -> None:
        if AppointmentService._PACKAGE_BILLING_SCHEMA_READY:
            return
        slot_columns = {col["name"] for col in inspect(db.bind).get_columns("patient_slot_booking")}
        for name, definition in {
            "patient_id": "INT NULL",
            "patient_package_id": "INT NULL",
            "program_id": "INT NULL",
            "duration_minutes": "INT NULL",
            "is_package_session": "BOOLEAN NOT NULL DEFAULT 0",
            "amount": "FLOAT NOT NULL DEFAULT 0",
            "paid_amount": "FLOAT NOT NULL DEFAULT 0",
            "due_amount": "FLOAT NOT NULL DEFAULT 0",
            "payment_status": "VARCHAR(50) NOT NULL DEFAULT 'UNPAID'",
        }.items():
            if name not in slot_columns:
                db.execute(text(f"ALTER TABLE patient_slot_booking ADD COLUMN {name} {definition}"))
        if "patient_id" not in slot_columns:
            db.execute(text("""
                UPDATE patient_slot_booking psb
                JOIN patient_session_plan_item pspi ON pspi.id = psb.patient_session_plan_item_id
                JOIN patient_session_plan psp ON psp.id = pspi.patient_session_plan_id
                SET psb.patient_id = psp.patient_id
                WHERE psb.patient_id IS NULL
            """))
        else:
            db.execute(text("""
                UPDATE patient_slot_booking psb
                JOIN patient_session_plan_item pspi ON pspi.id = psb.patient_session_plan_item_id
                JOIN patient_session_plan psp ON psp.id = pspi.patient_session_plan_id
                SET psb.patient_id = psp.patient_id
                WHERE psb.patient_id IS NULL
            """))
        package_columns = {col["name"] for col in inspect(db.bind).get_columns("patient_packages")}
        for name, definition in {
            "total_amount": "FLOAT NOT NULL DEFAULT 0",
            "paid_amount": "FLOAT NOT NULL DEFAULT 0",
            "due_amount": "FLOAT NOT NULL DEFAULT 0",
            "payment_status": "VARCHAR(50) NOT NULL DEFAULT 'UNPAID'",
        }.items():
            if name not in package_columns:
                db.execute(text(f"ALTER TABLE patient_packages ADD COLUMN {name} {definition}"))
        db.commit()
        AppointmentService._PACKAGE_BILLING_SCHEMA_READY = True

    @staticmethod
    def _ensure_program_schema(db: Session) -> None:
        if AppointmentService._PROGRAM_SCHEMA_READY:
            return

        inspector = inspect(db.bind)
        if not inspector.has_table("programs"):
            db.execute(text("""
                CREATE TABLE programs (
                    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    region_id INT NOT NULL,
                    program_name VARCHAR(100) NOT NULL,
                    per_session_amount FLOAT NOT NULL DEFAULT 1200,
                    duration_minutes INT NOT NULL DEFAULT 45,
                    capacity INT NOT NULL DEFAULT 1,
                    session_type VARCHAR(50) NOT NULL DEFAULT 'individual',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    deleted_at DATETIME NULL,
                    UNIQUE KEY uq_program_region_name (region_id, program_name),
                    KEY idx_program_region_id (region_id),
                    KEY idx_program_active (is_active),
                    CONSTRAINT fk_programs_region_id FOREIGN KEY (region_id) REFERENCES regions(id)
                )
            """))
        else:
            columns = {col["name"] for col in inspector.get_columns("programs")}
            for name, definition in {
                "region_id": "INT NOT NULL",
                "program_name": "VARCHAR(100) NOT NULL DEFAULT 'General'",
                "per_session_amount": "FLOAT NOT NULL DEFAULT 1200",
                "duration_minutes": "INT NOT NULL DEFAULT 45",
                "capacity": "INT NOT NULL DEFAULT 1",
                "session_type": "VARCHAR(50) NOT NULL DEFAULT 'individual'",
                "is_active": "BOOLEAN NOT NULL DEFAULT 1",
                "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
                "updated_at": "DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
                "deleted_at": "DATETIME NULL",
            }.items():
                if name not in columns:
                    db.execute(text(f"ALTER TABLE programs ADD COLUMN {name} {definition}"))

        db.execute(text("""
            INSERT INTO programs (region_id, program_name, per_session_amount, duration_minutes, capacity, session_type, is_active)
            SELECT r.id, program_seed.program_name, program_seed.per_session_amount, program_seed.duration_minutes, program_seed.capacity, program_seed.session_type, 1
            FROM regions r
            JOIN (
                SELECT 'General' AS program_name, 1200 AS per_session_amount, 45 AS duration_minutes, 1 AS capacity, 'individual' AS session_type
                UNION ALL SELECT 'CRT', 1800, 120, 1, 'structured'
                UNION ALL SELECT 'Vocational', 1000, 60, 10, 'group'
                UNION ALL SELECT 'Social Group', 900, 45, 8, 'group'
                UNION ALL SELECT 'Schooling', 800, 30, 1, 'individual'
            ) AS program_seed
            WHERE r.deleted_at IS NULL
            ON DUPLICATE KEY UPDATE
                is_active = VALUES(is_active),
                per_session_amount = programs.per_session_amount,
                duration_minutes = VALUES(duration_minutes),
                capacity = VALUES(capacity),
                session_type = VALUES(session_type)
        """))
        inspector = inspect(db.bind)
        if not inspector.has_table("program_segments"):
            db.execute(text("""
                CREATE TABLE program_segments (
                    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    program_id INT NOT NULL,
                    sequence_no INT NOT NULL,
                    label VARCHAR(100) NOT NULL,
                    duration_minutes INT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_program_segment_sequence (program_id, sequence_no),
                    KEY idx_program_segment_program_id (program_id),
                    KEY idx_program_segment_active (is_active),
                    CONSTRAINT fk_program_segments_program_id FOREIGN KEY (program_id) REFERENCES programs(id)
                )
            """))

        db.execute(text("""
            INSERT INTO program_segments (program_id, sequence_no, label, duration_minutes, is_active)
            SELECT p.id, segment_seed.sequence_no, segment_seed.label, segment_seed.duration_minutes, 1
            FROM programs p
            JOIN (
                SELECT 1 AS sequence_no, 'Therapist 1' AS label, 45 AS duration_minutes
                UNION ALL SELECT 2, 'Therapist 2', 45
                UNION ALL SELECT 3, 'Therapist 3', 30
            ) AS segment_seed
            WHERE LOWER(p.program_name) LIKE '%crt%'
            ON DUPLICATE KEY UPDATE
                label = VALUES(label),
                duration_minutes = VALUES(duration_minutes),
                is_active = VALUES(is_active)
        """))
        db.commit()
        AppointmentService._PROGRAM_SCHEMA_READY = True

    @staticmethod
    def _direct_program_amount(db: Session, patient_id: int | None) -> float:
        AppointmentService._ensure_program_schema(db)
        if not patient_id:
            return 0

        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        if not patient or not patient.region_id:
            return 0

        program = (
            db.query(Program)
            .filter(
                Program.region_id == patient.region_id,
                Program.program_name == "General",
                Program.is_active.is_(True),
                Program.deleted_at.is_(None),
            )
            .order_by(Program.id.asc())
            .first()
        )
        return float(program.per_session_amount or 0) if program else 0

    @staticmethod
    def _slot_booking_amount(slot_booking: PatientSlotBooking) -> float:
        if slot_booking.amount:
            return float(slot_booking.amount or 0)
        plan_item = slot_booking.patient_session_plan_item
        return float(plan_item.amount_per_session or 0) if plan_item else 0

    @staticmethod
    def _set_slot_payment_from_amounts(slot_booking: PatientSlotBooking, amount: float, paid: float, due: float, package_applied: float = 0) -> None:
        slot_booking.amount = amount
        slot_booking.due_amount = due
        if due <= 0 and amount > 0 and package_applied > 0:
            slot_booking.payment_status = "PACKAGE_COVERED"
        elif paid >= amount and amount > 0:
            slot_booking.payment_status = "PAID"
        elif paid > 0:
            slot_booking.payment_status = "PARTIAL"
        else:
            slot_booking.payment_status = "UNPAID"

    @staticmethod
    def _recalculate_package_slot_payments(db: Session, patient_package: PatientPackage | None) -> None:
        if not patient_package:
            return

        billable_status_ids = [
            AppointmentService.PATIENT_SLOT_BOOKING_COMPLETED_ID,
            AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
        ]
        package_credit = float(patient_package.paid_amount or 0)
        slot_bookings = (
            db.query(PatientSlotBooking)
            .filter(
                PatientSlotBooking.patient_package_id == patient_package.id,
                PatientSlotBooking.is_package_session.is_(True),
                PatientSlotBooking.status_id != AppointmentService.PATIENT_SLOT_BOOKING_UNPAID_CANCELLED_ID,
            )
            .order_by(PatientSlotBooking.created_at.asc(), PatientSlotBooking.id.asc())
            .all()
        )

        for slot_booking in slot_bookings:
            amount = AppointmentService._slot_booking_amount(slot_booking)
            if slot_booking.status_id not in billable_status_ids:
                AppointmentService._set_slot_payment_from_amounts(slot_booking, amount, float(slot_booking.paid_amount or 0), 0, 0)
                continue
            package_applied = min(package_credit, amount)
            package_credit = max(0, package_credit - package_applied)
            due = max(0, amount - package_applied)
            AppointmentService._set_slot_payment_from_amounts(slot_booking, amount, float(slot_booking.paid_amount or 0), due, package_applied)

    @staticmethod
    def _active_patient_package(db: Session, patient_id: int) -> PatientPackage | None:
        AppointmentService._ensure_package_billing_schema(db)
        return (
            db.query(PatientPackage)
            .filter(
                PatientPackage.patient_id == patient_id,
                PatientPackage.status == "active",
                PatientPackage.deleted_at.is_(None),
                PatientPackage.sessions_remaining > 0,
            )
            .order_by(PatientPackage.start_date.asc(), PatientPackage.id.asc())
            .with_for_update()
            .first()
        )

    @staticmethod
    def _time_to_minutes(value):
        return value.hour * 60 + value.minute

    @staticmethod
    def _slot_overlaps_break(slot) -> bool:
        start = AppointmentService._time_to_minutes(slot.start_time)
        end = AppointmentService._time_to_minutes(slot.end_time)
        return start < AppointmentService.BREAK_END_MINUTES and end > AppointmentService.BREAK_START_MINUTES

    @staticmethod
    def _program_type(program_name: str | None) -> str:
        name = (program_name or "General").strip().lower()
        if "crt" in name or "structured" in name:
            return "crt"
        if "vocational" in name:
            return "vocational"
        if "social" in name:
            return "social"
        if "school" in name:
            return "schooling"
        return "general"

    @staticmethod
    def _program_capacity(program_type: str) -> int | None:
        return {
            "vocational": 10,
            "social": 8,
            "crt": 1,
            "schooling": 1,
            "general": 1,
        }.get(program_type)

    @staticmethod
    def _program_default_duration(program_type: str) -> int:
        return {
            "crt": 120,
            "vocational": 60,
            "schooling": 30,
            "social": 45,
            "general": 45,
        }.get(program_type, 45)

    @staticmethod
    def _program_duration_from_row(row, program_type: str) -> int:
        return int(getattr(row, "program_duration_minutes", None) or AppointmentService._program_default_duration(program_type))

    @staticmethod
    def _program_capacity_from_row(row, program_type: str) -> int | None:
        return int(getattr(row, "program_capacity", None) or AppointmentService._program_capacity(program_type) or 1)

    @staticmethod
    def _add_minutes_to_time(value: str, minutes: int) -> str:
        if hasattr(value, "hour") and hasattr(value, "minute"):
            hour = value.hour
            minute = value.minute
        else:
            hour, minute = [int(part) for part in str(value)[:5].split(":")]
        total = hour * 60 + minute + minutes
        return f"{(total // 60) % 24:02d}:{total % 60:02d}"

    @staticmethod
    def _time_value_to_minutes(value) -> int:
        if hasattr(value, "hour") and hasattr(value, "minute"):
            return value.hour * 60 + value.minute
        hour, minute = [int(part) for part in str(value)[:5].split(":")]
        return hour * 60 + minute

    @staticmethod
    def _minutes_to_label(total: int) -> str:
        hour_24 = (total // 60) % 24
        minute = total % 60
        hour_12 = hour_24 % 12 or 12
        suffix = "PM" if hour_24 >= 12 else "AM"
        return f"{hour_12}:{minute:02d} {suffix}"

    @staticmethod
    def _program_duration_from_program(program: Program | None, program_type: str) -> int:
        return int(getattr(program, "duration_minutes", None) or AppointmentService._program_default_duration(program_type))

    @staticmethod
    def _booking_duration_for_program(db: Session, program_id: int | None) -> int:
        if not program_id:
            return AppointmentService._program_default_duration("general")
        program = db.query(Program).filter(Program.id == program_id).first()
        return AppointmentService._program_duration_from_program(
            program,
            AppointmentService._program_type(program.program_name if program else None),
        )

    @staticmethod
    def _validate_therapist_window_available(
        db: Session,
        *,
        therapist_id: int,
        slot_date,
        start_time,
        duration_minutes: int,
    ) -> None:
        new_start = AppointmentService._time_value_to_minutes(start_time)
        new_end = new_start + duration_minutes
        for _mapping, booking, existing_slot, existing_program in AppointmentRepository.get_active_therapist_slot_mappings_for_date(
            db,
            therapist_id=therapist_id,
            slot_date=slot_date,
        ):
            if not booking:
                continue
            existing_start = AppointmentService._time_value_to_minutes(existing_slot.start_time)
            existing_type = AppointmentService._program_type(existing_program.program_name if existing_program else None)
            existing_duration = int(
                getattr(booking, "duration_minutes", None)
                or (existing_slot.duration_minutes if existing_type == "crt" else AppointmentService._program_duration_from_program(existing_program, existing_type))
            )
            existing_end = existing_start + existing_duration
            if existing_start < new_end and existing_end > new_start:
                patient_name = None
                if booking and booking.patient:
                    patient_name = f"{booking.patient.first_name or ''} {booking.patient.last_name or ''}".strip()
                detail = f"{AppointmentService._minutes_to_label(existing_start)} - {AppointmentService._minutes_to_label(existing_end)}"
                if patient_name:
                    detail = f"{detail} for {patient_name}"
                raise ValueError(f"Therapist already has an appointment in this time window: {detail}")

    @staticmethod
    def _validate_patient_window_available(
        db: Session,
        *,
        patient_id: int,
        slot_date,
        start_time,
        duration_minutes: int,
    ) -> None:
        new_start = AppointmentService._time_value_to_minutes(start_time)
        new_end = new_start + duration_minutes
        for _booking, _mapping, existing_slot, existing_program in AppointmentRepository.get_active_patient_slot_bookings_for_date(
            db,
            patient_id=patient_id,
            slot_date=slot_date,
        ):
            existing_start = AppointmentService._time_value_to_minutes(existing_slot.start_time)
            existing_type = AppointmentService._program_type(existing_program.program_name if existing_program else None)
            existing_duration = int(
                getattr(_booking, "duration_minutes", None)
                or (existing_slot.duration_minutes if existing_type == "crt" else AppointmentService._program_duration_from_program(existing_program, existing_type))
            )
            existing_end = existing_start + existing_duration
            if existing_start < new_end and existing_end > new_start:
                raise ValueError(
                    "Child already has an appointment in this time window: "
                    f"{AppointmentService._minutes_to_label(existing_start)} - {AppointmentService._minutes_to_label(existing_end)}"
                )

    @staticmethod
    def _crt_segment_overrides(records):
        groups = defaultdict(list)
        for row in records:
            if (
                AppointmentService._program_type(row.program_name) == "crt"
                and row.patient_slot_booking_id
                and row.patient_id
                and row.program_id
            ):
                groups[(
                    row.patient_id,
                    row.program_id,
                    getattr(row, "slot_date", None),
                )].append(row)

        overrides = {}
        for rows in groups.values():
            for index, row in enumerate(sorted(rows, key=lambda item: item.start_time)):
                duration = 30 if index == 2 else 45
                overrides[row.patient_slot_booking_id] = {
                    "duration_minutes": duration,
                    "end_time": AppointmentService._add_minutes_to_time(row.start_time, duration),
                }
        return overrides

    @staticmethod
    def get_waitlist_patients(
        db: Session,
        current_user,
        region_id: int | None = None,
    ):
        # =========================
        # FETCH AVAILABLE PATIENTS
        # =========================

        patients = UserRepository.get_available_patients(
            db=db,
            current_user = current_user
        )
        if region_id:
            patients = [patient for patient in patients if patient.region_id == region_id]

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

        response.sort(key=lambda patient: (patient["full_name"] or "").strip().lower())

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
        current_user,
        region_id: int | None = None,
    ):

        return AppointmentRepository.get_therapists(
            db=db,
            therapy_id=therapy_id,
            region_ids=[region_id] if region_id else current_user.region_ids
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
        AppointmentService._ensure_package_billing_schema(db)
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
        if AppointmentService._slot_overlaps_break(slot):
            raise ValueError("Appointments cannot be booked during the 1:30 PM to 2:00 PM break")

        therapist_leave = AppointmentRepository.get_therapist_leave(
            db,
            booking_create.therapist_id,
            booking_create.slot_date,
        )
        if AppointmentService._leave_blocks_slot(therapist_leave, slot.start_time):
            raise ValueError("Therapist is not available")

        booking_duration = int(booking_create.duration_minutes or AppointmentService._booking_duration_for_program(db, booking_create.program_id))
        AppointmentService._validate_therapist_window_available(
            db,
            therapist_id=booking_create.therapist_id,
            slot_date=booking_create.slot_date,
            start_time=slot.start_time,
            duration_minutes=booking_duration,
        )

        AppointmentService._validate_patient_window_available(
            db,
            patient_id=booking_create.patient_id,
            slot_date=booking_create.slot_date,
            start_time=slot.start_time,
            duration_minutes=booking_duration,
        )

        plan_item = AppointmentRepository.get_plan_item_for_booking(
            db,
            booking_create.patient_id,
            booking_create.patient_session_plan_id,
            booking_create.therapy_id,
        )
        assigned_sessions = plan_item.assigned_sessions or 0 if plan_item else 0
        amount_per_session = float(plan_item.amount_per_session or 0) if plan_item else 0
        active_package = None
        if booking_create.use_package and booking_create.patient_package_id:
            active_package = (
                db.query(PatientPackage)
                .filter(
                    PatientPackage.id == booking_create.patient_package_id,
                    PatientPackage.patient_id == booking_create.patient_id,
                    PatientPackage.status == "active",
                    PatientPackage.deleted_at.is_(None),
                )
                .first()
            )
            if not active_package:
                raise ValueError("Selected package is not active for this child")
        elif booking_create.use_package:
            active_package = AppointmentService._active_patient_package(db, booking_create.patient_id)
        is_package_session = active_package is not None
        package_total = float(active_package.total_amount or 0) if active_package else 0
        package_paid = float(active_package.paid_amount or 0) if active_package else 0
        package_fully_paid = bool(active_package and package_total > 0 and package_paid >= package_total)
        booking_amount = amount_per_session
        booking_paid_amount = 0
        existing_package_usage = 0
        if active_package:
            billable_status_ids = [
                AppointmentService.PATIENT_SLOT_BOOKING_COMPLETED_ID,
                AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
            ]
            existing_package_usage = sum(
                AppointmentService._slot_booking_amount(row)
                for row in db.query(PatientSlotBooking)
                .filter(
                    PatientSlotBooking.patient_package_id == active_package.id,
                    PatientSlotBooking.is_package_session.is_(True),
                    PatientSlotBooking.status_id.in_(billable_status_ids),
                )
                .all()
            )
        available_package_credit = max(0, package_paid - existing_package_usage)
        booking_package_applied = min(available_package_credit, booking_amount) if is_package_session else 0
        booking_due_amount = 0 if is_package_session else max(0, booking_amount - booking_package_applied)
        booking_payment_status = "PACKAGE_COVERED" if is_package_session and package_fully_paid else "UNPAID"

        therapist_slot_mapping = AppointmentRepository.create_therapist_slot_mapping(
            db,
            therapist_id=booking_create.therapist_id,
            slot_id=booking_create.slot_id,
            slot_date=booking_create.slot_date,
            therapy_id=booking_create.therapy_id,
        )

        patient_slot_booking = AppointmentRepository.create_patient_slot_booking(
            db,
            patient_id=booking_create.patient_id,
            therapist_slot_mapping_id=therapist_slot_mapping.id,
            patient_session_plan_item_id=plan_item.id if plan_item else None,
            patient_package_id=active_package.id if active_package else None,
            program_id=booking_create.program_id,
            duration_minutes=booking_duration,
            is_package_session=is_package_session,
            amount=booking_amount,
            paid_amount=booking_paid_amount,
            due_amount=booking_due_amount,
            payment_status=booking_payment_status,
        )

        if plan_item:
            plan_item.assigned_sessions = assigned_sessions + 1
        if active_package:
            active_package.sessions_completed = (active_package.sessions_completed or 0) + 1
            active_package.sessions_remaining = max(0, (active_package.sessions_remaining or 0) - 1)
            if active_package.sessions_remaining == 0:
                active_package.status = "completed"
            AppointmentService._recalculate_package_slot_payments(db, active_package)
        db.commit()
        db.refresh(patient_slot_booking)
        db.refresh(therapist_slot_mapping)
        if plan_item:
            db.refresh(plan_item)
        if active_package:
            db.refresh(active_package)

        return {
            "patient_slot_booking": patient_slot_booking,
            "therapist_slot_mapping": therapist_slot_mapping,
            "patient_session_plan_item": plan_item,
        }

    @staticmethod
    def cancel_slot(db: Session, patient_slot_booking_id: int):
        patient_slot_booking = AppointmentRepository.get_patient_slot_booking_for_update(
            db,
            patient_slot_booking_id,
        )
        if not patient_slot_booking:
            raise ValueError("Slot booking not found")

        if patient_slot_booking.status in {"UNPAID_CANCELLED", "PAID_CANCELLED"}:
            raise ValueError("Slot booking already cancelled")

        if patient_slot_booking.status == "COMPLETED":
            raise ValueError("Completed slot cannot be cancelled")

        therapist_slot_mapping = patient_slot_booking.therapist_slot_mapping
        plan_item = patient_slot_booking.patient_session_plan_item
        package = patient_slot_booking.patient_package

        patient_slot_booking.status = "UNPAID_CANCELLED"
        if package and patient_slot_booking.is_package_session:
            package.sessions_completed = max(0, (package.sessions_completed or 0) - 1)
            package.sessions_remaining = (package.sessions_remaining or 0) + 1
            if package.status == "completed":
                package.status = "active"

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

        db.commit()
        db.refresh(patient_slot_booking)
        if therapist_slot_mapping:
            db.refresh(therapist_slot_mapping)
        if plan_item:
            db.refresh(plan_item)
        if package:
            db.refresh(package)

        return {
            "patient_slot_booking": patient_slot_booking,
            "therapist_slot_mapping": therapist_slot_mapping,
            "patient_session_plan_item": plan_item,
        }

    @staticmethod
    def reschedule_slot(db: Session, patient_slot_booking_id: int, booking_create: SlotBookingCreate):
        patient_slot_booking = AppointmentRepository.get_patient_slot_booking_for_update(
            db,
            patient_slot_booking_id,
        )
        if not patient_slot_booking:
            raise ValueError("Slot booking not found")

        if patient_slot_booking.status == "CANCELLED":
            raise ValueError("Cancelled slot cannot be edited")

        if patient_slot_booking.status == "COMPLETED":
            raise ValueError("Completed slot cannot be edited")

        current_mapping = patient_slot_booking.therapist_slot_mapping
        plan_item = patient_slot_booking.patient_session_plan_item
        if not current_mapping or not current_mapping.slot or not plan_item:
            raise ValueError("Slot booking details are incomplete")

        slot = AppointmentRepository.get_slot(db, booking_create.slot_id)
        if not slot:
            raise ValueError("Selected slot is not available")
        if AppointmentService._slot_overlaps_break(slot):
            raise ValueError("Appointments cannot be booked during the 1:30 PM to 2:00 PM break")

        therapist_leave = AppointmentRepository.get_therapist_leave(
            db,
            booking_create.therapist_id,
            booking_create.slot_date,
        )
        if AppointmentService._leave_blocks_slot(therapist_leave, slot.start_time):
            raise ValueError("Therapist is not available")

        occupied_mapping = (
            db.query(TherapistSlotMapping)
            .join(PatientSlotBooking, PatientSlotBooking.therapist_slot_mapping_id == TherapistSlotMapping.id)
            .filter(
                TherapistSlotMapping.therapist_id == booking_create.therapist_id,
                TherapistSlotMapping.slot_id == booking_create.slot_id,
                TherapistSlotMapping.slot_date == booking_create.slot_date,
                TherapistSlotMapping.therapy_id == booking_create.therapy_id,
                TherapistSlotMapping.status_id != AppointmentService.THERAPIST_SLOT_MAPPING_CANCELLED_ID,
                PatientSlotBooking.status_id.notin_([
                    AppointmentService.PATIENT_SLOT_BOOKING_UNPAID_CANCELLED_ID,
                    AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
                ]),
                PatientSlotBooking.id != patient_slot_booking_id,
            )
            .first()
        )
        if occupied_mapping:
            raise ValueError("Slot already booked for selected therapist and slot")

        current_mapping.therapist_id = booking_create.therapist_id
        current_mapping.slot_id = booking_create.slot_id
        current_mapping.slot_date = booking_create.slot_date
        current_mapping.therapy_id = booking_create.therapy_id

        db.commit()
        db.refresh(patient_slot_booking)
        db.refresh(current_mapping)
        db.refresh(plan_item)

        return {
            "patient_slot_booking": patient_slot_booking,
            "therapist_slot_mapping": current_mapping,
            "patient_session_plan_item": plan_item,
        }

    @staticmethod
    def _leave_blocks_slot(therapist_leave, slot_start) -> bool:
        if not therapist_leave:
            return False

        leave_session = str(getattr(therapist_leave, "leave_session", None) or "full_day").lower()
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
        AppointmentService._ensure_package_billing_schema(db)

        records = AppointmentRepository.get_calendar_data(
            db=db,
            selected_date=selected_date,
            region_ids=region_ids
        )

        leaves = AppointmentRepository.get_therapist_leaves(
            db=db,
            selected_date=selected_date
        )
        unavailable_rows = AppointmentRepository.get_unavailable_therapist_availability(
            db=db,
            selected_date=selected_date,
            region_ids=region_ids
        )

        leaves_by_therapist = {leave.therapist_id: leave for leave in leaves}
        for row in unavailable_rows:
            leaves_by_therapist.setdefault(row.therapist_id, row)

        crt_segment_overrides = AppointmentService._crt_segment_overrides(records)

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
            program_type = AppointmentService._program_type(row.program_name)
            segment_override = crt_segment_overrides.get(row.patient_slot_booking_id, {})
            program_duration = int(getattr(row, "booking_duration_minutes", None) or AppointmentService._program_duration_from_row(row, program_type)) if row.patient_slot_booking_id else row.duration_minutes
            display_duration = segment_override.get("duration_minutes", program_duration)
            display_end_time = segment_override.get(
                "end_time",
                AppointmentService._add_minutes_to_time(row.start_time, display_duration) if row.patient_slot_booking_id else row.end_time,
            )

            therapist_map[therapist_id]["therapist_id"] = therapist_id
            therapist_map[therapist_id]["therapist_name"] = therapist_name
            therapist_map[therapist_id]["therapy_name"] = row.therapy_name

            patient_id = row.patient_id
            patient_first_name = row.patient_first_name
            patient_last_name = row.patient_last_name
            patient_phone = row.patient_phone
            patient_name = None

            if patient_first_name:
                patient_name = (
                    f"{patient_first_name} "
                    f"{patient_last_name or ''}"
                )

            therapist_map[therapist_id]["slots"].append({
                "slot_mapping_id": row.slot_mapping_id,
                "patient_slot_booking_id": row.patient_slot_booking_id,
                "slot_id": row.slot_id,
                "slot_date": str(selected_date),
                "slot_time": row.start_time,
                "start_time": row.start_time,
                "end_time": display_end_time,
                "duration_minutes": display_duration,
                "therapy_id": row.therapy_id,
                "therapy_name": row.therapy_name,
                "patient_id": patient_id,
                "patient_name": patient_name,
                "patient_code": f"CP-{patient_id}" if patient_id else None,
                "patient_phone": patient_phone,
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
                "patient_package_id": row.patient_package_id,
                "is_package_session": bool(row.is_package_session),
                "package_name": row.package_name,
                "package_total_amount": float(row.package_total_amount or 0),
                "package_paid_amount": float(row.package_paid_amount or 0),
                "package_due_amount": float(row.package_due_amount or 0),
                "package_payment_status": row.package_payment_status,
                "amount": float(row.amount or 0),
                "paid_amount": float(row.paid_amount or 0),
                "due_amount": float(row.due_amount or 0),
                "payment_status": row.payment_status,
                "program_id": row.program_id,
                "program_name": row.program_name,
                "program_type": program_type,
                "program_duration_minutes": program_duration if row.patient_slot_booking_id else None,
                "program_session_type": getattr(row, "program_session_type", None),
                "child_count": 1 if patient_id else 0,
                "capacity": AppointmentService._program_capacity_from_row(row, program_type),
                "participants": [{
                    "id": patient_id,
                    "full_name": patient_name,
                    "patient_code": f"CP-{patient_id}",
                }] if patient_id else [],
                "package_warning": remaining_sessions <= 0,
                "leave_session": getattr(leave, "leave_session", "full_day") if leave and is_leave_slot else None,
                "leave_reason": (getattr(leave, "reason", None) or getattr(leave, "notes", None)) if leave and is_leave_slot else None,
                "patient_slot_booking_status": STATUS_ID_TO_CODE["patient_slot_booking"].get(
                    row.patient_slot_booking_status_id
                ),
                "therapist_slot_mapping_status": STATUS_ID_TO_CODE["therapist_slot_mapping"].get(
                    row.therapist_slot_mapping_status_id
                ),
                "status": "Leave" if is_leave_slot else ("Booked" if row.patient_slot_booking_id else "Available"),
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
                    "package_total_amount": 0,
                    "package_paid_amount": 0,
                    "package_due_amount": 0,
                    "package_payment_status": None,
                    "leave_session": getattr(leave, "leave_session", "full_day"),
                    "leave_reason": getattr(leave, "reason", None) or getattr(leave, "notes", None),
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
    def get_calendar_range_view(
        db,
        start_date,
        end_date,
        region_ids
    ):
        AppointmentService._ensure_package_billing_schema(db)

        records = AppointmentRepository.get_calendar_data_range(
            db=db,
            start_date=start_date,
            end_date=end_date,
            region_ids=region_ids
        )

        leaves = AppointmentRepository.get_therapist_leaves_range(
            db=db,
            start_date=start_date,
            end_date=end_date,
        )
        leaves_by_therapist_date = {
            (leave.therapist_id, leave.leave_date): leave
            for leave in leaves
        }
        crt_segment_overrides = AppointmentService._crt_segment_overrides(records)

        therapist_map = defaultdict(lambda: {
            "therapist_id": None,
            "therapist_name": None,
            "therapy_name": None,
            "slots": []
        })

        active_therapists = AppointmentRepository.get_active_therapists_for_calendar(db, region_ids)
        for therapist in active_therapists:
            therapist_map[therapist.id]["therapist_id"] = therapist.id
            therapist_map[therapist.id]["therapist_name"] = therapist.name
            therapist_map[therapist.id]["therapy_name"] = None

        for row in records:
            therapist_id = row.therapist_id
            slot_date = row.slot_date
            leave = leaves_by_therapist_date.get((therapist_id, slot_date))
            is_leave_slot = AppointmentService._leave_blocks_slot(leave, row.start_time)
            assigned_sessions = row.assigned_sessions or 0
            completed_sessions = row.completed_sessions or 0
            allocated_sessions = row.allocated_sessions or 0
            remaining_sessions = max(0, allocated_sessions - assigned_sessions - completed_sessions)
            program_type = AppointmentService._program_type(row.program_name)
            segment_override = crt_segment_overrides.get(row.patient_slot_booking_id, {})
            program_duration = int(getattr(row, "booking_duration_minutes", None) or AppointmentService._program_duration_from_row(row, program_type)) if row.patient_slot_booking_id else row.duration_minutes
            display_duration = segment_override.get("duration_minutes", program_duration)
            display_end_time = segment_override.get(
                "end_time",
                AppointmentService._add_minutes_to_time(row.start_time, display_duration) if row.patient_slot_booking_id else row.end_time,
            )

            therapist_map[therapist_id]["therapist_id"] = therapist_id
            therapist_map[therapist_id]["therapist_name"] = row.therapist_name
            therapist_map[therapist_id]["therapy_name"] = row.therapy_name

            patient_id = row.patient_id
            patient_first_name = row.patient_first_name
            patient_last_name = row.patient_last_name
            patient_phone = row.patient_phone
            patient_name = None
            if patient_first_name:
                patient_name = (
                    f"{patient_first_name} "
                    f"{patient_last_name or ''}"
                )

            therapist_map[therapist_id]["slots"].append({
                "slot_mapping_id": row.slot_mapping_id,
                "patient_slot_booking_id": row.patient_slot_booking_id,
                "slot_id": row.slot_id,
                "slot_date": str(slot_date),
                "slot_time": row.start_time,
                "start_time": row.start_time,
                "end_time": display_end_time,
                "duration_minutes": display_duration,
                "therapy_id": row.therapy_id,
                "therapy_name": row.therapy_name,
                "patient_id": patient_id,
                "patient_name": patient_name,
                "patient_code": f"CP-{patient_id}" if patient_id else None,
                "patient_phone": patient_phone,
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
                "patient_package_id": row.patient_package_id,
                "is_package_session": bool(row.is_package_session),
                "package_name": row.package_name,
                "package_total_amount": float(row.package_total_amount or 0),
                "package_paid_amount": float(row.package_paid_amount or 0),
                "package_due_amount": float(row.package_due_amount or 0),
                "package_payment_status": row.package_payment_status,
                "amount": float(row.amount or 0),
                "paid_amount": float(row.paid_amount or 0),
                "due_amount": float(row.due_amount or 0),
                "payment_status": row.payment_status,
                "program_id": row.program_id,
                "program_name": row.program_name,
                "program_type": program_type,
                "program_duration_minutes": program_duration if row.patient_slot_booking_id else None,
                "program_session_type": getattr(row, "program_session_type", None),
                "child_count": 1 if patient_id else 0,
                "capacity": AppointmentService._program_capacity_from_row(row, program_type),
                "participants": [{
                    "id": patient_id,
                    "full_name": patient_name,
                    "patient_code": f"CP-{patient_id}",
                }] if patient_id else [],
                "package_warning": remaining_sessions <= 0,
                "leave_session": getattr(leave, "leave_session", "full_day") if leave and is_leave_slot else None,
                "leave_reason": (getattr(leave, "reason", None) or getattr(leave, "notes", None)) if leave and is_leave_slot else None,
                "patient_slot_booking_status": STATUS_ID_TO_CODE["patient_slot_booking"].get(
                    row.patient_slot_booking_status_id
                ),
                "therapist_slot_mapping_status": STATUS_ID_TO_CODE["therapist_slot_mapping"].get(
                    row.therapist_slot_mapping_status_id
                ),
                "status": "Leave" if is_leave_slot else ("Booked" if row.patient_slot_booking_id else "Available"),
            })

        calendar_slots = db.query(SlotMaster).filter(SlotMaster.is_active == 1).all()
        current_date = start_date
        while current_date <= end_date:
            for therapist in active_therapists:
                leave = leaves_by_therapist_date.get((therapist.id, current_date))
                if not leave:
                    continue
                existing_leave_keys = {
                    (slot["slot_date"], slot["slot_id"], slot["status"])
                    for slot in therapist_map[therapist.id]["slots"]
                }
                for slot in calendar_slots:
                    if not AppointmentService._leave_blocks_slot(leave, slot.start_time):
                        continue
                    slot_key = (str(current_date), slot.id, "Leave")
                    if slot_key in existing_leave_keys:
                        continue
                    therapist_map[therapist.id]["slots"].append({
                        "slot_mapping_id": None,
                        "patient_slot_booking_id": None,
                        "slot_id": slot.id,
                        "slot_date": str(current_date),
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
                        "package_total_amount": 0,
                        "package_paid_amount": 0,
                        "package_due_amount": 0,
                        "package_payment_status": None,
                        "leave_session": getattr(leave, "leave_session", "full_day"),
                        "leave_reason": getattr(leave, "reason", None) or getattr(leave, "notes", None),
                        "patient_slot_booking_status": None,
                        "therapist_slot_mapping_status": None,
                        "status": "Leave",
                    })
            current_date += timedelta(days=1)

        for therapist in therapist_map.values():
            therapist["slots"].sort(key=lambda slot: (slot["slot_date"], slot["start_time"]))

        return {
            "start_date": str(start_date),
            "end_date": str(end_date),
            "therapists": list(therapist_map.values())
        }
    
    @staticmethod
    def get_appointment_by_id(db: Session, appointment_id: int, region_id: int = None) -> Appointment:
        """Get appointment by ID with region filtering"""
        return AppointmentRepository.get_by_id(db, appointment_id, region_id)

    @staticmethod
    def update_slot_status(
        db: Session,
        patient_slot_booking_id: int,
        action: str,
        cancel_type: str | None = None,
    ):
        patient_slot_booking = AppointmentRepository.get_patient_slot_booking_for_update(
            db,
            patient_slot_booking_id,
        )
        if not patient_slot_booking:
            raise ValueError("Slot booking not found")

        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"complete", "cancel"}:
            raise ValueError("Invalid slot action")

        current_status = str(patient_slot_booking.status or "").upper()
        therapist_slot_mapping = patient_slot_booking.therapist_slot_mapping
        plan_item = patient_slot_booking.patient_session_plan_item
        package = patient_slot_booking.patient_package

        if current_status in {"UNPAID_CANCELLED", "PAID_CANCELLED"}:
            raise ValueError("Slot booking already cancelled")

        if normalized_action == "complete" and current_status == "COMPLETED":
            raise ValueError("Slot booking already completed")

        if normalized_action == "complete":
            patient_slot_booking.status = "COMPLETED"
            if therapist_slot_mapping:
                therapist_slot_mapping.status = "COMPLETED"
            if plan_item:
                assigned_sessions = plan_item.assigned_sessions or 0
                completed_sessions = plan_item.completed_sessions or 0
                if assigned_sessions > 0:
                    plan_item.assigned_sessions = assigned_sessions - 1
                plan_item.completed_sessions = completed_sessions + 1
        else:
            normalized_cancel_type = str(cancel_type or "unpaid").strip().lower()
            patient_slot_booking.status = "PAID_CANCELLED" if normalized_cancel_type == "paid" else "UNPAID_CANCELLED"
            if package and patient_slot_booking.is_package_session:
                package.sessions_completed = max(0, (package.sessions_completed or 0) - 1)
                package.sessions_remaining = (package.sessions_remaining or 0) + 1
                if package.status == "completed":
                    package.status = "active"
            if therapist_slot_mapping and not AppointmentRepository.has_active_patient_booking_for_mapping(
                db,
                therapist_slot_mapping.id,
                exclude_patient_slot_booking_id=patient_slot_booking.id,
            ):
                therapist_slot_mapping.status = "CANCELLED"
            if plan_item and normalized_cancel_type != "paid":
                assigned_sessions = plan_item.assigned_sessions or 0
                if assigned_sessions > 0:
                    plan_item.assigned_sessions = assigned_sessions - 1

        slot_amount = float(patient_slot_booking.amount or 0)
        if slot_amount <= 0 and plan_item:
            slot_amount = float(plan_item.amount_per_session or 0)
            patient_slot_booking.amount = slot_amount
        if normalized_action == "complete" and not patient_slot_booking.is_package_session and slot_amount <= 0:
            slot_amount = AppointmentService._direct_program_amount(db, patient_slot_booking.patient_id)
            patient_slot_booking.amount = slot_amount
        package_total = float(package.total_amount or 0) if package else 0
        package_paid = float(package.paid_amount or 0) if package else 0
        package_fully_paid = bool(patient_slot_booking.is_package_session and package and package_total > 0 and package_paid >= package_total)
        paid_amount = float(patient_slot_booking.paid_amount or 0)
        if package and patient_slot_booking.is_package_session:
            AppointmentService._recalculate_package_slot_payments(db, package)
        else:
            patient_slot_booking.due_amount = 0 if package_fully_paid else max(0, slot_amount - paid_amount)
            if package_fully_paid:
                patient_slot_booking.payment_status = "PACKAGE_COVERED"
            elif paid_amount >= slot_amount and slot_amount > 0:
                patient_slot_booking.payment_status = "PAID"
            elif paid_amount > 0:
                patient_slot_booking.payment_status = "PARTIAL"
            else:
                patient_slot_booking.payment_status = "UNPAID"

        db.commit()
        db.refresh(patient_slot_booking)
        if therapist_slot_mapping:
            db.refresh(therapist_slot_mapping)
        if plan_item:
            db.refresh(plan_item)
        if package:
            db.refresh(package)

        remaining_sessions = None
        if plan_item:
            remaining_sessions = (
                (plan_item.allocated_sessions or 0)
                - (plan_item.assigned_sessions or 0)
                - (plan_item.completed_sessions or 0)
            )

        return {
            "success": True,
            "message": "Slot marked as completed successfully" if normalized_action == "complete" else "Slot cancelled successfully",
            "appointment_id": None,
            "appointment_status": None,
            "patient_slot_booking_id": patient_slot_booking.id,
            "patient_slot_booking_status": patient_slot_booking.status,
            "therapist_slot_mapping_id": therapist_slot_mapping.id if therapist_slot_mapping else None,
            "therapist_slot_mapping_status": therapist_slot_mapping.status if therapist_slot_mapping else None,
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
    def _ensure_quarter_hour_slots(db: Session):
        existing = {
            slot.start_time.strftime("%H:%M")
            for slot in db.query(SlotMaster).filter(SlotMaster.is_active == 1).all()
        }
        created = False
        day_start = 9 * 60
        day_end = 18 * 60

        default_duration = 45
        for minute in range(day_start, day_end, 15):
            start = time(hour=minute // 60, minute=minute % 60)
            end_minute = minute + default_duration
            if end_minute > 23 * 60 + 59:
                continue
            end = time(hour=end_minute // 60, minute=end_minute % 60)
            key = start.strftime("%H:%M")
            if key in existing:
                continue
            db.add(
                SlotMaster(
                    slot_name=f"{start.strftime('%H:%M')} - {end.strftime('%H:%M')}",
                    start_time=start,
                    end_time=end,
                    duration_minutes=default_duration,
                    is_active=True,
                )
            )
            created = True

        if created:
            db.commit()

    @staticmethod
    def get_all_slots(db: Session):
        SlotMasterService._ensure_quarter_hour_slots(db)

        slots = (
            db.query(SlotMaster)
            .filter(SlotMaster.is_active == 1)
            .order_by(SlotMaster.start_time.asc())
            .all()
        )

        return slots
    
    
