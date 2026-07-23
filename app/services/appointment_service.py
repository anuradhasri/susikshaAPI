from collections import defaultdict
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, inspect, or_, text
from datetime import datetime, date, time, timedelta
from app.models.models import Appointment, Session as DBSession, SessionNote, SlotMaster, Therapist, Patient, PatientPackage, Package, Payment, Program, ProgramSegment, STATUS_ID_TO_CODE, MASTER_LOOKUP_DATA, PatientSlotBooking, TherapistSlotMapping, CrtProgramBooking, GroupProgramBooking, PatientAssessmentBilling
from app.repositories.appointment_repository import AppointmentRepository
from app.schemas.schemas import AppointmentCreate, AppointmentUpdate, SessionCreate, SessionUpdate, SlotBookingCreate
from app.utils.query_utils import soft_delete, filter_by_region
from app.services.patient_service import PatientService
from app.repositories.user_repository import UserRepository


class AppointmentService:
    """Service for appointment operations"""
    BREAK_START_MINUTES = 13 * 60 + 30
    BREAK_END_MINUTES = 14 * 60
    PATIENT_SLOT_BOOKING_BOOKED_ID = MASTER_LOOKUP_DATA["patient_slot_booking"]["BOOKED"]
    PATIENT_SLOT_BOOKING_UNPAID_CANCELLED_ID = MASTER_LOOKUP_DATA["patient_slot_booking"]["UNPAID_CANCELLED"]
    PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID = MASTER_LOOKUP_DATA["patient_slot_booking"]["PAID_CANCELLED"]
    PATIENT_SLOT_BOOKING_COMPLETED_ID = MASTER_LOOKUP_DATA["patient_slot_booking"]["COMPLETED"]
    THERAPIST_SLOT_MAPPING_CANCELLED_ID = MASTER_LOOKUP_DATA["therapist_slot_mapping"]["CANCELLED"]
    THERAPIST_SLOT_MAPPING_COMPLETED_ID = MASTER_LOOKUP_DATA["therapist_slot_mapping"]["COMPLETED"]
    _PACKAGE_BILLING_SCHEMA_READY = False
    _PROGRAM_SCHEMA_READY = False

    @staticmethod
    def _has_table(db: Session, table_name: str) -> bool:
        return inspect(db.bind).has_table(table_name)

    @staticmethod
    def _legacy_calendar_slot(row, slot_date):
        start_time = row.start_time.time() if row.start_time else None
        end_time = row.end_time.time() if row.end_time else None
        patient_name = " ".join(
            part for part in [row.patient_first_name, row.patient_last_name] if part
        ).strip() or None
        duration_minutes = int((row.end_time - row.start_time).total_seconds() / 60) if row.start_time and row.end_time else 0

        return {
            "slot_mapping_id": None,
            "therapist_id": row.therapist_id,
            "therapist_name": row.therapist_name,
            "patient_slot_booking_id": None,
            "crt_program_booking_id": None,
            "group_program_booking_id": None,
            "slot_id": row.appointment_id,
            "slot_date": str(slot_date),
            "slot_time": start_time,
            "start_time": start_time,
            "end_time": end_time,
            "duration_minutes": duration_minutes,
            "therapy_id": None,
            "therapy_name": None,
            "patient_id": row.patient_id,
            "patient_name": patient_name,
            "patient_code": f"CP-{row.patient_id}" if row.patient_id else None,
            "patient_phone": row.patient_phone,
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
            "patient_package_id": None,
            "is_package_session": False,
            "package_name": None,
            "package_total_amount": 0,
            "package_paid_amount": 0,
            "package_due_amount": 0,
            "package_payment_status": None,
            "amount": 0,
            "paid_amount": 0,
            "due_amount": 0,
            "fully_paid": False,
            "payment_status": None,
            "program_id": None,
            "program_name": None,
            "program_type": "general",
            "program_duration_minutes": None,
            "program_session_type": None,
            "child_count": 1 if row.patient_id else 0,
            "capacity": 1,
            "participants": [{
                "id": row.patient_id,
                "full_name": patient_name,
                "patient_code": f"CP-{row.patient_id}",
                "patient_slot_booking_id": None,
                "crt_program_booking_id": None,
                "group_program_booking_id": None,
                "patient_session_plan_id": None,
                "patient_phone": row.patient_phone,
                "amount": 0,
                "paid_amount": 0,
                "due_amount": 0,
                "fully_paid": False,
                "payment_status": None,
                "status": row.status,
                "patient_package_id": None,
                "is_package_session": False,
                "package_name": None,
            }] if row.patient_id else [],
            "package_warning": False,
            "leave_session": None,
            "leave_reason": None,
            "patient_slot_booking_status": row.status,
            "therapist_slot_mapping_status": None,
            "status": "Booked" if str(row.status or "").upper() not in {"CANCELLED", "NO_SHOW"} else row.status,
        }

    @staticmethod
    def _legacy_calendar_view(db: Session, start_date, end_date, region_ids):
        therapist_map = defaultdict(lambda: {
            "therapist_id": None,
            "therapist_name": None,
            "therapy_name": None,
            "slots": []
        })

        therapists = AppointmentRepository.get_active_therapists_for_calendar(db, region_ids)
        for therapist in therapists:
            therapist_map[therapist.id]["therapist_id"] = therapist.id
            therapist_map[therapist.id]["therapist_name"] = therapist.name
            therapist_map[therapist.id]["therapy_name"] = None

        region_filter = ""
        params = {"start_date": start_date, "end_date": end_date}
        if region_ids:
            region_values = ", ".join(str(int(region_id)) for region_id in region_ids)
            region_filter = f"AND a.region_id IN ({region_values})"

        rows = db.execute(text(f"""
            SELECT
                a.id AS appointment_id,
                a.patient_id,
                a.therapist_id,
                a.start_time,
                a.end_time,
                a.status,
                p.first_name AS patient_first_name,
                p.last_name AS patient_last_name,
                p.phone AS patient_phone,
                CONCAT(u.first_name, ' ', u.last_name) AS therapist_name
            FROM appointments a
            JOIN patients p ON p.id = a.patient_id
            JOIN therapists t ON t.id = a.therapist_id
            LEFT JOIN users u ON u.id = t.user_id
            WHERE a.deleted_at IS NULL
              AND DATE(a.start_time) >= :start_date
              AND DATE(a.start_time) <= :end_date
              {region_filter}
            ORDER BY a.start_time, a.id
        """), params).mappings().all()

        for row in rows:
            slot_date = row.start_time.date() if row.start_time else start_date
            therapist_map[row.therapist_id]["therapist_id"] = row.therapist_id
            therapist_map[row.therapist_id]["therapist_name"] = row.therapist_name or f"Therapist {row.therapist_id}"
            therapist_map[row.therapist_id]["therapy_name"] = None
            therapist_map[row.therapist_id]["slots"].append(AppointmentService._legacy_calendar_slot(row, slot_date))

        for therapist in therapist_map.values():
            therapist["slots"].sort(key=lambda slot: (slot["slot_date"], slot["start_time"]))

        return list(therapist_map.values())

    @staticmethod
    def _ensure_package_billing_schema(db: Session) -> None:
        if AppointmentService._PACKAGE_BILLING_SCHEMA_READY:
            slot_columns = {col["name"] for col in inspect(db.bind).get_columns("patient_slot_booking")}
            if "crt_program_booking_id" not in slot_columns:
                db.execute(text("ALTER TABLE patient_slot_booking ADD COLUMN crt_program_booking_id INT NULL"))
            if "group_program_booking_id" not in slot_columns:
                db.execute(text("ALTER TABLE patient_slot_booking ADD COLUMN group_program_booking_id INT NULL"))
            AppointmentService._ensure_crt_program_booking_schema(db)
            AppointmentService._ensure_group_program_booking_schema(db)
            AppointmentService._backfill_crt_program_bookings(db)
            AppointmentService._ensure_group_booking_indexes(db)
            if "fully_paid" in slot_columns:
                db.commit()
                return
            db.execute(text("ALTER TABLE patient_slot_booking ADD COLUMN fully_paid BOOLEAN NOT NULL DEFAULT 0"))
            db.commit()
            return
        slot_columns = {col["name"] for col in inspect(db.bind).get_columns("patient_slot_booking")}
        for name, definition in {
            "patient_id": "INT NULL",
            "patient_package_id": "INT NULL",
            "crt_program_booking_id": "INT NULL",
            "group_program_booking_id": "INT NULL",
            "program_id": "INT NULL",
            "duration_minutes": "INT NULL",
            "is_package_session": "BOOLEAN NOT NULL DEFAULT 0",
            "amount": "FLOAT NOT NULL DEFAULT 0",
            "paid_amount": "FLOAT NOT NULL DEFAULT 0",
            "due_amount": "FLOAT NOT NULL DEFAULT 0",
            "fully_paid": "BOOLEAN NOT NULL DEFAULT 0",
            "payment_status": "VARCHAR(50) NOT NULL DEFAULT 'UNPAID'",
        }.items():
            if name not in slot_columns:
                db.execute(text(f"ALTER TABLE patient_slot_booking ADD COLUMN {name} {definition}"))
        AppointmentService._ensure_crt_program_booking_schema(db)
        AppointmentService._ensure_group_program_booking_schema(db)
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
        AppointmentService._ensure_group_booking_indexes(db)
        AppointmentService._backfill_crt_program_bookings(db)
        db.commit()
        AppointmentService._PACKAGE_BILLING_SCHEMA_READY = True

    @staticmethod
    def _ensure_crt_program_booking_schema(db: Session) -> None:
        inspector = inspect(db.bind)
        if inspector.has_table("crt_program_bookings"):
            columns = {col["name"] for col in inspector.get_columns("crt_program_bookings")}
            if "slot_id" not in columns:
                db.execute(text("ALTER TABLE crt_program_bookings ADD COLUMN slot_id INT NULL"))
            return
        db.execute(text("""
            CREATE TABLE crt_program_bookings (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                patient_id INT NOT NULL,
                program_id INT NOT NULL,
                patient_package_id INT NULL,
                slot_id INT NULL,
                slot_date DATE NOT NULL,
                total_amount FLOAT NOT NULL DEFAULT 0,
                paid_amount FLOAT NOT NULL DEFAULT 0,
                due_amount FLOAT NOT NULL DEFAULT 0,
                fully_paid BOOLEAN NOT NULL DEFAULT 0,
                is_package_session BOOLEAN NOT NULL DEFAULT 0,
                payment_status VARCHAR(50) NOT NULL DEFAULT 'UNPAID',
                status_id INT NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_crt_program_booking_patient_date (patient_id, program_id, slot_date),
                KEY idx_crt_program_booking_session (patient_id, program_id, slot_date, slot_id),
                KEY idx_crt_program_booking_package_id (patient_package_id),
                KEY idx_crt_program_booking_status_id (status_id)
            )
        """))

    @staticmethod
    def _ensure_group_program_booking_schema(db: Session) -> None:
        inspector = inspect(db.bind)
        if inspector.has_table("group_program_bookings"):
            return
        db.execute(text("""
            CREATE TABLE group_program_bookings (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                program_id INT NOT NULL,
                region_id INT NULL,
                therapy_id INT NULL,
                slot_id INT NOT NULL,
                slot_date DATE NOT NULL,
                start_time TIME NULL,
                end_time TIME NULL,
                status_id INT NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_group_program_booking_session (program_id, slot_date, slot_id, therapy_id),
                KEY idx_group_program_booking_region_id (region_id),
                KEY idx_group_program_booking_status_id (status_id)
            )
        """))

    @staticmethod
    def _ensure_group_booking_indexes(db: Session) -> None:
        inspector = inspect(db.bind)
        indexes = {idx["name"] for idx in inspector.get_indexes("patient_slot_booking")}
        unique_constraints = {idx["name"] for idx in inspector.get_unique_constraints("patient_slot_booking")}
        existing_names = indexes | unique_constraints
        dialect = db.bind.dialect.name if db.bind else ""
        if "uq_one_patient_per_slot" in existing_names:
            if dialect == "mysql":
                if "idx_patient_slot_booking_mapping_id" not in existing_names:
                    db.execute(text(
                        "CREATE INDEX idx_patient_slot_booking_mapping_id "
                        "ON patient_slot_booking (therapist_slot_mapping_id)"
                    ))
                    existing_names.add("idx_patient_slot_booking_mapping_id")
                db.execute(text("ALTER TABLE patient_slot_booking DROP INDEX uq_one_patient_per_slot"))
            else:
                db.execute(text("DROP INDEX IF EXISTS uq_one_patient_per_slot"))
            existing_names.discard("uq_one_patient_per_slot")
        if "uq_patient_slot_mapping_child" not in existing_names:
            if dialect == "sqlite":
                db.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_slot_mapping_child "
                    "ON patient_slot_booking (therapist_slot_mapping_id, patient_id)"
                ))
            else:
                db.execute(text(
                    "ALTER TABLE patient_slot_booking "
                    "ADD CONSTRAINT uq_patient_slot_mapping_child "
                    "UNIQUE (therapist_slot_mapping_id, patient_id)"
                ))

    @staticmethod
    def _backfill_crt_program_bookings(db: Session) -> None:
        existing_parents = (
            db.query(CrtProgramBooking)
            .filter(CrtProgramBooking.slot_id.is_(None))
            .all()
        )
        for parent in existing_parents:
            linked_slot = (
                db.query(PatientSlotBooking)
                .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
                .filter(PatientSlotBooking.crt_program_booking_id == parent.id)
                .order_by(TherapistSlotMapping.slot_id.asc(), PatientSlotBooking.id.asc())
                .first()
            )
            if linked_slot and linked_slot.therapist_slot_mapping:
                parent.slot_id = linked_slot.therapist_slot_mapping.slot_id

        crt_slots = (
            db.query(PatientSlotBooking)
            .join(Program, Program.id == PatientSlotBooking.program_id)
            .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
            .filter(
                PatientSlotBooking.crt_program_booking_id.is_(None),
                PatientSlotBooking.patient_id.isnot(None),
                or_(Program.program_name.ilike("%crt%"), Program.program_name.ilike("%structured%")),
            )
            .all()
        )
        groups: dict[tuple[int, int, date], list[PatientSlotBooking]] = defaultdict(list)
        for slot_booking in crt_slots:
            if slot_booking.therapist_slot_mapping and slot_booking.program_id:
                groups[(
                    slot_booking.patient_id,
                    slot_booking.program_id,
                    slot_booking.therapist_slot_mapping.slot_date,
                )].append(slot_booking)

        for (patient_id, program_id, slot_date), group_slots in groups.items():
            first_mapping = group_slots[0].therapist_slot_mapping
            amount = max(float(AppointmentService._slot_booking_amount(slot) or 0) for slot in group_slots)
            paid = sum(float(slot.paid_amount or 0) for slot in group_slots)
            fully_paid = any(bool(getattr(slot, "fully_paid", False)) for slot in group_slots) or (amount > 0 and paid >= amount)
            parent = CrtProgramBooking(
                patient_id=patient_id,
                program_id=program_id,
                patient_package_id=next((slot.patient_package_id for slot in group_slots if slot.patient_package_id), None),
                slot_id=first_mapping.slot_id if first_mapping else None,
                slot_date=slot_date,
                total_amount=amount,
                paid_amount=paid,
                due_amount=0 if fully_paid else max(0, amount - paid),
                fully_paid=fully_paid,
                is_package_session=any(bool(slot.is_package_session) for slot in group_slots),
                payment_status="PAID" if fully_paid else ("PARTIAL" if paid > 0 else "UNPAID"),
                status_id=group_slots[0].status_id,
            )
            db.add(parent)
            db.flush()
            payable_slot = max(group_slots, key=lambda slot: (float(AppointmentService._slot_booking_amount(slot) or 0), int(slot.id or 0)))
            for slot in group_slots:
                slot.crt_program_booking_id = parent.id
                slot.payment_status = parent.payment_status
                slot.fully_paid = parent.fully_paid
                if slot.id != payable_slot.id:
                    slot.amount = 0
                    slot.paid_amount = 0
                    slot.due_amount = 0
            payable_slot.amount = amount
            payable_slot.paid_amount = paid
            payable_slot.due_amount = parent.due_amount

    @staticmethod
    def _get_or_create_group_program_booking(
        db: Session,
        *,
        program_id: int,
        region_id: int | None,
        therapy_id: int | None,
        slot_id: int,
        slot_date,
        start_time,
        end_time,
    ) -> GroupProgramBooking:
        group_booking = (
            db.query(GroupProgramBooking)
            .filter(
                GroupProgramBooking.program_id == program_id,
                GroupProgramBooking.slot_id == slot_id,
                GroupProgramBooking.slot_date == slot_date,
                GroupProgramBooking.therapy_id == therapy_id,
                GroupProgramBooking.status_id.notin_([
                    AppointmentService.PATIENT_SLOT_BOOKING_UNPAID_CANCELLED_ID,
                    AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
                ]),
            )
            .order_by(GroupProgramBooking.id.asc())
            .first()
        )
        if group_booking:
            return group_booking

        group_booking = GroupProgramBooking(
            program_id=program_id,
            region_id=region_id,
            therapy_id=therapy_id,
            slot_id=slot_id,
            slot_date=slot_date,
            start_time=start_time,
            end_time=end_time,
            status_id=MASTER_LOOKUP_DATA["patient_slot_booking"]["BOOKED"],
        )
        db.add(group_booking)
        db.flush()
        return group_booking

    @staticmethod
    def _ensure_program_schema(db: Session, force_seed: bool = False) -> None:
        if AppointmentService._PROGRAM_SCHEMA_READY and not force_seed:
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
                UNION ALL SELECT 'Academic Intervention', 1200, 45, 1, 'individual'
                UNION ALL SELECT 'Sushiksha Online', 1200, 45, 1, 'individual'
                UNION ALL SELECT 'CRT', 1800, 120, 1, 'structured'
                UNION ALL SELECT 'Vocational', 1200, 60, 10, 'group'
                UNION ALL SELECT 'Social Group', 1200, 60, 8, 'group'
                UNION ALL SELECT 'Parent Training Program: Prewriting Skills', 1200, 60, 10, 'group'
                UNION ALL SELECT 'Parent Training Program: Toilet Training', 1200, 60, 10, 'group'
                UNION ALL SELECT 'Parent Training Program: PEG', 1200, 60, 10, 'group'
                UNION ALL SELECT 'Schooling', 800, 30, 1, 'individual'
            ) AS program_seed
            WHERE r.deleted_at IS NULL
            ON DUPLICATE KEY UPDATE
                is_active = VALUES(is_active),
                duration_minutes = VALUES(duration_minutes),
                capacity = VALUES(capacity),
                session_type = VALUES(session_type),
                deleted_at = NULL
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
        patient_package = slot_booking.patient_package
        if slot_booking.is_package_session and patient_package and patient_package.package and patient_package.package.total_sessions:
            return float(patient_package.total_amount or patient_package.package.price or 0) / float(patient_package.package.total_sessions or 1)
        if slot_booking.amount:
            return float(slot_booking.amount or 0)
        plan_item = slot_booking.patient_session_plan_item
        if plan_item and plan_item.amount_per_session:
            return float(plan_item.amount_per_session or 0)
        if slot_booking.program and slot_booking.program.per_session_amount:
            return float(slot_booking.program.per_session_amount or 0)
        return 0

    @staticmethod
    def _crt_payable_slot(db: Session, slot_booking: PatientSlotBooking) -> PatientSlotBooking:
        if not slot_booking.crt_program_booking_id:
            return slot_booking
        siblings = (
            db.query(PatientSlotBooking)
            .filter(PatientSlotBooking.crt_program_booking_id == slot_booking.crt_program_booking_id)
            .order_by(PatientSlotBooking.id.asc())
            .all()
        )
        if not siblings:
            return slot_booking
        mapping = slot_booking.therapist_slot_mapping
        slot = mapping.slot if mapping else None
        if mapping and getattr(mapping, "slot_date", None) and slot:
            same_session_siblings = [
                row
                for row in siblings
                if getattr(getattr(row, "therapist_slot_mapping", None), "slot_date", None) == getattr(mapping, "slot_date", None)
                and getattr(getattr(getattr(row, "therapist_slot_mapping", None), "slot", None), "start_time", None) == getattr(slot, "start_time", None)
                and getattr(getattr(getattr(row, "therapist_slot_mapping", None), "slot", None), "end_time", None) == getattr(slot, "end_time", None)
            ]
            if same_session_siblings:
                siblings = same_session_siblings
        return max(siblings, key=lambda row: (float(row.amount or 0), -int(row.id or 0)))

    @staticmethod
    def _set_slot_payment_from_amounts(slot_booking: PatientSlotBooking, amount: float, paid: float, due: float, package_applied: float = 0) -> None:
        slot_booking.amount = amount
        slot_booking.paid_amount = paid
        slot_booking.package_covered_amount = package_applied
        slot_booking.due_amount = due
        if not bool(getattr(slot_booking, "is_package_session", False)) and not getattr(slot_booking, "patient_package_id", None):
            slot_booking.fully_paid = bool(amount > 0 and due <= 0)
        if due <= 0 and amount > 0 and package_applied > 0:
            slot_booking.payment_status = "PACKAGE_COVERED"
        elif due <= 0 and amount > 0:
            slot_booking.payment_status = "PAID"
        elif paid >= amount and amount > 0:
            slot_booking.payment_status = "PAID"
        elif package_applied > 0:
            slot_booking.payment_status = "PARTIAL"
        elif paid > 0:
            slot_booking.payment_status = "PARTIAL"
        else:
            slot_booking.payment_status = "UNPAID"

    @staticmethod
    def _set_crt_payment_from_amounts(crt_parent: CrtProgramBooking | None, amount: float, paid: float, due: float, package_applied: float = 0) -> None:
        if not crt_parent:
            return
        crt_parent.total_amount = amount
        crt_parent.paid_amount = paid
        crt_parent.package_covered_amount = package_applied
        crt_parent.due_amount = due
        crt_parent.fully_paid = bool(crt_parent.fully_paid or (amount > 0 and due <= 0))
        if due <= 0 and amount > 0 and package_applied > 0:
            crt_parent.payment_status = "PACKAGE_COVERED"
        elif due <= 0 and amount > 0:
            crt_parent.payment_status = "PAID"
        elif paid >= amount and amount > 0:
            crt_parent.payment_status = "PAID"
        elif package_applied > 0 or paid > 0:
            crt_parent.payment_status = "PARTIAL"
        else:
            crt_parent.payment_status = "UNPAID"

    @staticmethod
    def _slot_waived_amount(db: Session, slot_booking: PatientSlotBooking) -> float:
        filters = []
        if slot_booking.id:
            filters.append(Payment.patient_slot_booking_id == slot_booking.id)
            filters.append(Payment.remark.like(f"[slot:{slot_booking.id}]%"))
        if slot_booking.crt_program_booking_id:
            filters.append(Payment.crt_program_booking_id == slot_booking.crt_program_booking_id)
        if not filters:
            return 0.0
        patient_id = slot_booking.patient_id
        if not patient_id and slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
            patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
        payments = (
            db.query(Payment)
            .filter(
                Payment.patient_id == patient_id,
                Payment.fully_paid.is_(True),
                Payment.payment_note.like("%Marked as fully paid%"),
                or_(*filters),
            )
            .all()
        )
        return sum(
            AppointmentService._payment_waived_amount(payment)
            for payment in payments
        )

    @staticmethod
    def _slot_marked_fully_paid(db: Session, slot_booking: PatientSlotBooking) -> bool:
        filters = []
        if slot_booking.id:
            filters.append(Payment.patient_slot_booking_id == slot_booking.id)
            filters.append(Payment.remark.like(f"[slot:{slot_booking.id}]%"))
        if slot_booking.crt_program_booking_id:
            filters.append(Payment.crt_program_booking_id == slot_booking.crt_program_booking_id)
        if not filters:
            return False
        patient_id = slot_booking.patient_id
        if not patient_id and slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
            patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
        if not patient_id:
            return False
        return db.query(Payment.id).filter(
            Payment.patient_id == patient_id,
            Payment.fully_paid.is_(True),
            Payment.payment_note.like("%Marked as fully paid%"),
            or_(*filters),
        ).first() is not None

    @staticmethod
    def _sync_patient_package_due_from_ledger(db: Session, patient_package: PatientPackage | None) -> None:
        if not patient_package:
            return
        usage_rows = (
            db.query(PatientSlotBooking)
            .filter(
                PatientSlotBooking.patient_package_id == patient_package.id,
                PatientSlotBooking.is_package_session.is_(True),
                PatientSlotBooking.status_id.in_([
                    AppointmentService.PATIENT_SLOT_BOOKING_COMPLETED_ID,
                    AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
                ]),
            )
            .all()
        )
        slot_ids = [row.id for row in usage_rows if row.id]
        crt_ids = list({int(row.crt_program_booking_id) for row in usage_rows if row.crt_program_booking_id})
        related_filters = [Payment.remark.like(f"[package:{patient_package.id}]%")]
        if slot_ids:
            related_filters.append(Payment.patient_slot_booking_id.in_(slot_ids))
            related_filters.extend(Payment.remark.like(f"[slot:{slot_id}]%") for slot_id in slot_ids)
        if crt_ids:
            related_filters.append(Payment.crt_program_booking_id.in_(crt_ids))
        payments = db.query(Payment).filter(
            Payment.patient_id == patient_package.patient_id,
            or_(*related_filters),
        ).all()
        positive_due = [
            float(payment.due_amount or 0)
            for payment in payments
            if not bool(getattr(payment, "fully_paid", False))
            and float(payment.due_amount or 0) > 0
            and not str(payment.remark or "").startswith(f"[package:{patient_package.id}]")
        ]
        if positive_due:
            patient_package.due_amount = max(positive_due)
            return
        credit_due = [
            float(payment.due_amount or 0)
            for payment in payments
            if float(payment.due_amount or 0) < 0
        ]
        patient_package.due_amount = min(credit_due) if credit_due else 0

    @staticmethod
    def _payment_waived_amount(payment: Payment) -> float:
        note = str(getattr(payment, "payment_note", "") or "")
        match = re.search(r"Waived Rs\s*([0-9]+(?:\.[0-9]+)?)", note, flags=re.I)
        if match:
            return max(0.0, float(match.group(1) or 0))
        return max(0.0, float(payment.amount or 0) - float(payment.payment_amount or 0))

    @staticmethod
    def _sync_package_credit_payment_row(db: Session, slot_booking: PatientSlotBooking, amount: float, due: float) -> None:
        patient_id = slot_booking.patient_id
        if not patient_id and slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
            patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
        if not patient_id and slot_booking.patient_package:
            patient_id = slot_booking.patient_package.patient_id
        if not patient_id:
            return
        db.add(Payment(
            patient_id=patient_id,
            patient_slot_booking_id=slot_booking.id if not slot_booking.crt_program_booking_id else None,
            crt_program_booking_id=slot_booking.crt_program_booking_id,
            amount=amount,
            payment_amount=0,
            payment_status="PAID",
            fully_paid=False,
            due_amount=due,
            payment_date=datetime.now(),
            remark=f"[slot:{slot_booking.id}] Payment done for Package credit used",
            payment_note=f"Allocated from package payment {slot_booking.patient_package_id}",
        ))

    @staticmethod
    def _package_direct_paid_amount(db: Session, patient_package: PatientPackage) -> float:
        if not patient_package or not patient_package.id:
            return 0.0
        total = (
            db.query(func.coalesce(func.sum(Payment.payment_amount), 0))
            .filter(
                Payment.patient_id == patient_package.patient_id,
                Payment.payment_amount > 0,
                Payment.remark.like(f"[package:{patient_package.id}]%"),
            )
            .scalar()
        )
        return float(total or 0)

    @staticmethod
    def _recalculate_package_slot_payments(
        db: Session,
        patient_package: PatientPackage | None,
        *,
        record_credit_payments: bool = False,
    ) -> None:
        if not patient_package:
            return

        completed_status_id = AppointmentService.PATIENT_SLOT_BOOKING_COMPLETED_ID
        billable_status_ids = [
            completed_status_id,
            AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
        ]
        all_slot_bookings = (
            db.query(PatientSlotBooking)
            .filter(
                PatientSlotBooking.patient_package_id == patient_package.id,
                PatientSlotBooking.is_package_session.is_(True),
                PatientSlotBooking.status_id.in_(billable_status_ids),
            )
            .order_by(PatientSlotBooking.created_at.asc(), PatientSlotBooking.id.asc())
            .all()
        )
        slot_bookings: list[PatientSlotBooking] = []
        emitted_crt_keys: set[tuple] = set()
        for slot_booking in all_slot_bookings:
            if slot_booking.crt_program_booking_id:
                mapping = slot_booking.therapist_slot_mapping
                slot = mapping.slot if mapping else None
                crt_key = (
                    int(slot_booking.crt_program_booking_id),
                    getattr(mapping, "slot_date", None),
                    getattr(slot, "start_time", None),
                    getattr(slot, "end_time", None),
                    0
                    if mapping and getattr(mapping, "slot_date", None) and slot
                    else int(slot_booking.id or 0),
                )
                if crt_key in emitted_crt_keys:
                    continue
                emitted_crt_keys.add(crt_key)
                siblings = [
                    row
                    for row in all_slot_bookings
                    if row.crt_program_booking_id == slot_booking.crt_program_booking_id
                    and (
                        int(row.id or 0) == int(slot_booking.id or 0)
                        or (
                            getattr(getattr(row, "therapist_slot_mapping", None), "slot_date", None) == getattr(mapping, "slot_date", None)
                            and getattr(getattr(getattr(row, "therapist_slot_mapping", None), "slot", None), "start_time", None) == getattr(slot, "start_time", None)
                            and getattr(getattr(getattr(row, "therapist_slot_mapping", None), "slot", None), "end_time", None) == getattr(slot, "end_time", None)
                        )
                    )
                ]
                payable_slot = max(siblings, key=lambda row: (float(row.amount or 0), -int(row.id or 0)))
                slot_bookings.append(payable_slot)
            else:
                slot_bookings.append(slot_booking)

        used_amount = 0.0
        slot_paid_amount = 0.0
        package_credit_remaining = AppointmentService._package_direct_paid_amount(db, patient_package)
        for slot_booking in slot_bookings:
            crt_parent = slot_booking.crt_program_booking
            amount = float((crt_parent.total_amount if crt_parent else None) or AppointmentService._slot_booking_amount(slot_booking) or 0)
            if slot_booking.status_id != completed_status_id:
                if crt_parent:
                    AppointmentService._set_crt_payment_from_amounts(
                        crt_parent,
                        amount,
                        float(crt_parent.paid_amount or 0),
                        float(crt_parent.due_amount or 0),
                        float(crt_parent.package_covered_amount or 0),
                    )
                else:
                    AppointmentService._set_slot_payment_from_amounts(slot_booking, amount, float(slot_booking.paid_amount or 0), 0, 0)
                continue
            paid = float((crt_parent.paid_amount if crt_parent else slot_booking.paid_amount) or 0)
            marked_fully_paid = AppointmentService._slot_marked_fully_paid(db, slot_booking)
            if marked_fully_paid:
                existing_covered = min(amount, float((crt_parent.package_covered_amount if crt_parent else getattr(slot_booking, "package_covered_amount", 0)) or 0))
                package_credit_remaining = max(0, package_credit_remaining - existing_covered)
                settled_amount = min(amount, paid + existing_covered)
                if crt_parent:
                    AppointmentService._set_crt_payment_from_amounts(crt_parent, amount, paid, 0, existing_covered)
                    crt_parent.fully_paid = True
                    crt_parent.payment_status = "PAID"
                else:
                    AppointmentService._set_slot_payment_from_amounts(slot_booking, amount, paid, 0, existing_covered)
                    slot_booking.fully_paid = True
                    slot_booking.payment_status = "PAID"
                used_amount += settled_amount
                slot_paid_amount += paid
                continue
            package_applied = min(package_credit_remaining, max(0, amount - paid))
            package_credit_remaining = max(0, package_credit_remaining - package_applied)
            due = max(0, amount - paid - package_applied)
            if crt_parent:
                AppointmentService._set_crt_payment_from_amounts(crt_parent, amount, paid, due, package_applied)
            else:
                AppointmentService._set_slot_payment_from_amounts(slot_booking, amount, paid, due, package_applied)
            used_amount += amount
            slot_paid_amount += paid

        patient_package.paid_amount = AppointmentService._package_direct_paid_amount(db, patient_package) + slot_paid_amount
        patient_package.due_amount = used_amount - float(patient_package.paid_amount or 0)

    @staticmethod
    def _record_slot_payment_if_missing(
        db: Session,
        slot_booking: PatientSlotBooking,
        *,
        amount: float,
        due_amount: float,
        remark_text: str,
    ) -> None:
        if amount < 0:
            return
        patient_id = slot_booking.patient_id
        if not patient_id and slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
            patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
        if not patient_id:
            return
        if remark_text == "Package credit used":
            AppointmentService._sync_package_credit_payment_row(db, slot_booking, amount, due_amount)
            return
        marker = f"[slot:{slot_booking.id}]"
        stored_amount = amount + due_amount
        if slot_booking.crt_program_booking and float(slot_booking.crt_program_booking.total_amount or 0) > 0:
            stored_amount = float(slot_booking.crt_program_booking.total_amount or 0)
        elif float(slot_booking.amount or 0) > 0:
            stored_amount = float(slot_booking.amount or 0)
        if remark_text == "Package credit used":
            stored_amount = AppointmentService._slot_booking_amount(slot_booking)
        db.add(Payment(
            patient_id=patient_id,
            patient_slot_booking_id=slot_booking.id if not slot_booking.crt_program_booking_id else None,
            crt_program_booking_id=slot_booking.crt_program_booking_id,
            amount=stored_amount,
            payment_amount=amount,
            payment_status="PAID",
            fully_paid=False if remark_text == "Session completed" else due_amount <= 0,
            due_amount=due_amount,
            payment_date=datetime.utcnow(),
            remark=f"{marker} {remark_text}",
        ))

    @staticmethod
    def _patient_normal_session_due(db: Session, slot_booking: PatientSlotBooking) -> float:
        patient_id = slot_booking.patient_id
        if not patient_id and slot_booking.patient_session_plan_item and slot_booking.patient_session_plan_item.patient_session_plan:
            patient_id = slot_booking.patient_session_plan_item.patient_session_plan.patient_id
        if not patient_id:
            return float(slot_booking.due_amount or 0)

        billable_status_ids = [
            AppointmentService.PATIENT_SLOT_BOOKING_BOOKED_ID,
            AppointmentService.PATIENT_SLOT_BOOKING_COMPLETED_ID,
            AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
        ]
        total_due = (
            db.query(func.coalesce(func.sum(PatientSlotBooking.due_amount), 0))
            .filter(
                PatientSlotBooking.patient_id == patient_id,
                PatientSlotBooking.is_package_session.is_(False),
                PatientSlotBooking.crt_program_booking_id.is_(None),
                PatientSlotBooking.status_id.in_(billable_status_ids),
                PatientSlotBooking.fully_paid.is_(False),
                PatientSlotBooking.due_amount > 0,
            )
            .scalar()
        )
        return float(total_due or 0)

    @staticmethod
    def _patient_due_buckets(db: Session, patient_ids: set[int]) -> dict[int, dict[str, float]]:
        buckets = {
            int(patient_id): {
                "session_due_amount": 0.0,
                "package_due_amount": 0.0,
                "assessment_due_amount": 0.0,
            }
            for patient_id in patient_ids
            if patient_id
        }
        if not buckets:
            return buckets

        patient_id_values = list(buckets.keys())
        billable_status_ids = [
            AppointmentService.PATIENT_SLOT_BOOKING_COMPLETED_ID,
            AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
        ]

        slot_due_rows = (
            db.query(PatientSlotBooking.patient_id, func.coalesce(func.sum(PatientSlotBooking.due_amount), 0))
            .filter(
                PatientSlotBooking.patient_id.in_(patient_id_values),
                PatientSlotBooking.is_package_session.is_(False),
                PatientSlotBooking.crt_program_booking_id.is_(None),
                PatientSlotBooking.group_program_booking_id.is_(None),
                PatientSlotBooking.status_id.in_(billable_status_ids),
                PatientSlotBooking.fully_paid.is_(False),
                PatientSlotBooking.due_amount > 0,
            )
            .group_by(PatientSlotBooking.patient_id)
            .all()
        )
        for patient_id, due_amount in slot_due_rows:
            buckets[int(patient_id)]["session_due_amount"] += float(due_amount or 0)

        crt_due_rows = (
            db.query(CrtProgramBooking.patient_id, func.coalesce(func.sum(CrtProgramBooking.due_amount), 0))
            .filter(
                CrtProgramBooking.patient_id.in_(patient_id_values),
                CrtProgramBooking.is_package_session.is_(False),
                CrtProgramBooking.fully_paid.is_(False),
                CrtProgramBooking.due_amount > 0,
            )
            .group_by(CrtProgramBooking.patient_id)
            .all()
        )
        for patient_id, due_amount in crt_due_rows:
            buckets[int(patient_id)]["session_due_amount"] += float(due_amount or 0)

        package_rows = (
            db.query(PatientPackage)
            .filter(
                PatientPackage.patient_id.in_(patient_id_values),
                PatientPackage.deleted_at.is_(None),
            )
            .all()
        )
        for patient_package in package_rows:
            AppointmentService._recalculate_package_slot_payments(db, patient_package)
            due_amount = max(0, float(patient_package.due_amount or 0))
            if due_amount > 0:
                buckets[int(patient_package.patient_id)]["package_due_amount"] += due_amount

        assessment_due_rows = (
            db.query(PatientAssessmentBilling.patient_id, func.coalesce(func.sum(PatientAssessmentBilling.due_amount), 0))
            .filter(
                PatientAssessmentBilling.patient_id.in_(patient_id_values),
                PatientAssessmentBilling.fully_paid.is_(False),
                PatientAssessmentBilling.due_amount > 0,
            )
            .group_by(PatientAssessmentBilling.patient_id)
            .all()
        )
        for patient_id, due_amount in assessment_due_rows:
            buckets[int(patient_id)]["assessment_due_amount"] = float(due_amount or 0)

        return buckets

    @staticmethod
    def _slot_current_payment_state(
        db: Session,
        slot_booking_id: int | None,
        crt_program_booking_id: int | None,
        amount: float,
        stored_due: float,
        payment_status: str | None,
        is_package_session: bool = False,
        package_covered_amount: float = 0.0,
    ) -> dict[str, float | bool]:
        filters = []
        if slot_booking_id:
            filters.append(Payment.patient_slot_booking_id == slot_booking_id)
            filters.append(Payment.remark.like(f"[slot:{slot_booking_id}]%"))
        if crt_program_booking_id:
            filters.append(Payment.crt_program_booking_id == crt_program_booking_id)

        latest_payment = None
        if filters:
            latest_payment = (
                db.query(Payment)
                .filter(or_(*filters))
                .order_by(Payment.created_at.desc(), Payment.payment_date.desc(), Payment.id.desc())
                .first()
            )

        status = str(payment_status or "").upper()
        if latest_payment:
            if is_package_session:
                paid_total = float(
                    db.query(func.coalesce(func.sum(Payment.payment_amount), 0))
                    .filter(or_(*filters))
                    .scalar()
                    or 0
                )
                current_due = max(0.0, float(amount or 0) - float(package_covered_amount or 0) - paid_total)
                current_fully_paid = bool(getattr(latest_payment, "fully_paid", False)) or (float(amount or 0) > 0 and current_due <= 0)
                return {
                    "current_due_amount": 0.0 if current_fully_paid else current_due,
                    "current_fully_paid": current_fully_paid,
                    "current_paid_amount": paid_total,
                }
            latest_due = float(latest_payment.due_amount or 0)
            latest_paid = float(latest_payment.payment_amount or 0)
            latest_fully_paid = bool(getattr(latest_payment, "fully_paid", False))
            current_fully_paid = latest_fully_paid or latest_due <= 0 or status in {"PAID", "PACKAGE_COVERED"}
            return {
                "current_due_amount": 0.0 if current_fully_paid else min(max(0.0, latest_due), max(0.0, amount or latest_due)),
                "current_fully_paid": current_fully_paid,
                "current_paid_amount": latest_paid,
            }

        current_fully_paid = status in {"PAID", "PACKAGE_COVERED"} or (amount > 0 and stored_due <= 0)
        return {
            "current_due_amount": 0.0 if current_fully_paid else min(max(0.0, stored_due), max(0.0, amount or stored_due)),
            "current_fully_paid": current_fully_paid,
            "current_paid_amount": 0.0,
        }

    @staticmethod
    def _package_remaining_credit(db: Session, patient_package: PatientPackage | None) -> float:
        if not patient_package:
            return 0.0

        billable_status_ids = [
            AppointmentService.PATIENT_SLOT_BOOKING_BOOKED_ID,
            AppointmentService.PATIENT_SLOT_BOOKING_COMPLETED_ID,
            AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
        ]
        slot_bookings = (
            db.query(PatientSlotBooking)
            .filter(
                PatientSlotBooking.patient_package_id == patient_package.id,
                PatientSlotBooking.is_package_session.is_(True),
                PatientSlotBooking.status_id.in_(billable_status_ids),
            )
            .order_by(PatientSlotBooking.created_at.asc(), PatientSlotBooking.id.asc())
            .all()
        )
        rows_by_crt_parent: dict[int, PatientSlotBooking] = {}
        rows_without_crt: list[PatientSlotBooking] = []
        for slot_booking in slot_bookings:
            if slot_booking.crt_program_booking_id:
                parent_id = int(slot_booking.crt_program_booking_id)
                current = rows_by_crt_parent.get(parent_id)
                if not current or AppointmentService._slot_booking_amount(slot_booking) > AppointmentService._slot_booking_amount(current):
                    rows_by_crt_parent[parent_id] = slot_booking
            else:
                rows_without_crt.append(slot_booking)
        utilized = sum(AppointmentService._slot_booking_amount(slot_booking) for slot_booking in [*rows_by_crt_parent.values(), *rows_without_crt])
        return float(patient_package.paid_amount or 0) - utilized

    @staticmethod
    def _package_signed_due(db: Session, patient_package: PatientPackage | None) -> float:
        if not patient_package:
            return 0.0

        billable_status_ids = [
            AppointmentService.PATIENT_SLOT_BOOKING_BOOKED_ID,
            AppointmentService.PATIENT_SLOT_BOOKING_COMPLETED_ID,
            AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
        ]
        slot_bookings = (
            db.query(PatientSlotBooking)
            .filter(
                PatientSlotBooking.patient_package_id == patient_package.id,
                PatientSlotBooking.is_package_session.is_(True),
                PatientSlotBooking.status_id.in_(billable_status_ids),
            )
            .order_by(PatientSlotBooking.created_at.asc(), PatientSlotBooking.id.asc())
            .all()
        )
        rows_by_crt_parent: dict[int, PatientSlotBooking] = {}
        rows_without_crt: list[PatientSlotBooking] = []
        for slot_booking in slot_bookings:
            if slot_booking.crt_program_booking_id:
                parent_id = int(slot_booking.crt_program_booking_id)
                current = rows_by_crt_parent.get(parent_id)
                if not current or AppointmentService._slot_booking_amount(slot_booking) > AppointmentService._slot_booking_amount(current):
                    rows_by_crt_parent[parent_id] = slot_booking
            else:
                rows_without_crt.append(slot_booking)
        utilized = sum(AppointmentService._slot_booking_amount(slot_booking) for slot_booking in [*rows_by_crt_parent.values(), *rows_without_crt])
        return utilized - float(patient_package.paid_amount or 0)

    @staticmethod
    def _sync_patient_package_completion_status(patient_package: PatientPackage | None) -> None:
        if not patient_package:
            return
        sessions_remaining = int(patient_package.sessions_remaining or 0)
        due_amount = float(patient_package.due_amount or 0)
        patient_package.status = "completed" if sessions_remaining <= 0 and due_amount <= 0 else "active"

    @staticmethod
    def _latest_package_payment_due(db: Session, patient_package: PatientPackage | None) -> float | None:
        if not patient_package:
            return None
        slot_ids = [
            row.id
            for row in db.query(PatientSlotBooking.id)
            .filter(PatientSlotBooking.patient_package_id == patient_package.id)
            .all()
        ]
        crt_ids = [
            row.crt_program_booking_id
            for row in db.query(PatientSlotBooking.crt_program_booking_id)
            .filter(
                PatientSlotBooking.patient_package_id == patient_package.id,
                PatientSlotBooking.crt_program_booking_id.isnot(None),
            )
            .distinct()
            .all()
        ]
        filters = [Payment.remark.like(f"[package:{patient_package.id}]%")]
        if slot_ids:
            filters.append(Payment.patient_slot_booking_id.in_(slot_ids))
        if crt_ids:
            filters.append(Payment.crt_program_booking_id.in_(crt_ids))
        latest_payment = (
            db.query(Payment)
            .filter(
                Payment.patient_id == patient_package.patient_id,
                or_(*filters),
            )
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .first()
        )
        return float(latest_payment.due_amount) if latest_payment else None

    @staticmethod
    def _package_with_credit_for_slot(db: Session, slot_booking: PatientSlotBooking) -> PatientPackage | None:
        if not slot_booking.patient_id or not slot_booking.program_id:
            return None
        packages = (
            db.query(PatientPackage)
            .join(Package, Package.id == PatientPackage.package_id)
            .filter(
                PatientPackage.patient_id == slot_booking.patient_id,
                PatientPackage.deleted_at.is_(None),
                Package.program_id == slot_booking.program_id,
            )
            .order_by(PatientPackage.start_date.asc(), PatientPackage.id.asc())
            .with_for_update()
            .all()
        )
        for patient_package in packages:
            if AppointmentService._package_remaining_credit(db, patient_package) > 0:
                return patient_package
        return None

    @staticmethod
    def _active_patient_package(db: Session, patient_id: int, program_type: str | None = None) -> PatientPackage | None:
        AppointmentService._ensure_package_billing_schema(db)
        packages = (
            db.query(PatientPackage)
            .join(PatientPackage.package)
            .filter(
                PatientPackage.patient_id == patient_id,
                PatientPackage.status == "active",
                PatientPackage.deleted_at.is_(None),
                PatientPackage.sessions_remaining > 0,
            )
            .order_by(PatientPackage.start_date.asc(), PatientPackage.id.asc())
            .with_for_update()
            .all()
        )
        if program_type:
            for patient_package in packages:
                package = patient_package.package
                package_program_name = package.program.program_name if package and package.program else None
                package_name = package.name if package else None
                if AppointmentService._program_type(package_program_name or package_name) == program_type:
                    return patient_package
            return None
        return packages[0] if packages else None

    @staticmethod
    def _auto_package_rule(program_type: str | None) -> dict | None:
        return {
            "crt": {
                "name": "CRT 5 Session - Package",
                "total_sessions": 5,
                "price": 45000,
                "duration_days": 30,
            },
            "vocational": {
                "name": "Vocational 8 Session - Package",
                "total_sessions": 8,
                "price": 10000,
                "duration_days": 60,
            },
        }.get(program_type or "")

    @staticmethod
    def _get_or_create_package_master_for_program(db: Session, program: Program, program_type: str) -> Package | None:
        rule = AppointmentService._auto_package_rule(program_type)
        if not rule or not program:
            return None

        package = (
            db.query(Package)
            .filter(
                Package.program_id == program.id,
                Package.name == rule["name"],
                Package.deleted_at.is_(None),
            )
            .order_by(Package.id.asc())
            .first()
        )
        if package:
            package.total_sessions = rule["total_sessions"]
            package.price = rule["price"]
            package.duration_days = rule["duration_days"]
            package.region_id = program.region_id
            package.is_active = True
            return package

        package = Package(
            name=rule["name"],
            description=f"{program.program_name} package",
            region_id=program.region_id,
            program_id=program.id,
            total_sessions=rule["total_sessions"],
            price=rule["price"],
            duration_days=rule["duration_days"],
            is_active=True,
        )
        db.add(package)
        db.flush()
        return package

    @staticmethod
    def _get_or_create_auto_patient_package(
        db: Session,
        *,
        patient_id: int,
        program: Program | None,
        program_type: str,
        start_date,
    ) -> PatientPackage | None:
        rule = AppointmentService._auto_package_rule(program_type)
        if not rule or not program:
            return None

        package = AppointmentService._get_or_create_package_master_for_program(db, program, program_type)
        if not package:
            return None

        existing = AppointmentService._active_patient_package(db, patient_id, program_type)
        if existing:
            return existing

        duration_days = int(package.duration_days or rule["duration_days"] or 0)
        end_date = start_date + timedelta(days=duration_days) if start_date and duration_days else None
        patient_package = PatientPackage(
            patient_id=patient_id,
            package_id=package.id,
            start_date=start_date,
            end_date=end_date,
            sessions_completed=0,
            sessions_remaining=int(package.total_sessions or rule["total_sessions"]),
            total_amount=float(package.price or rule["price"]),
            paid_amount=0,
            due_amount=0,
            fully_paid=False,
            payment_status="UNPAID",
            status="active",
        )
        db.add(patient_package)
        db.flush()
        return patient_package

    @staticmethod
    def _time_to_minutes(value):
        return value.hour * 60 + value.minute

    @staticmethod
    def _slot_overlaps_break(slot) -> bool:
        start = AppointmentService._time_to_minutes(slot.start_time)
        end = AppointmentService._time_to_minutes(slot.end_time)
        return start < AppointmentService.BREAK_END_MINUTES and end > AppointmentService.BREAK_START_MINUTES

    @staticmethod
    def _booking_overlaps_break(slot, duration_minutes: int | None = None) -> bool:
        start = AppointmentService._time_to_minutes(slot.start_time)
        end = start + int(duration_minutes or 0) if duration_minutes else AppointmentService._time_to_minutes(slot.end_time)
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
        if "peg" in name:
            return "peg"
        if "parent training" in name or "prewriting" in name or "prewritting" in name or "toilet" in name:
            return "parent_training"
        if "school" in name:
            return "schooling"
        return "general"

    @staticmethod
    def _normalized_program_name(value: str | None) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split())

    @staticmethod
    def _package_matches_program(patient_package: PatientPackage | None, program: Program | None, program_type: str) -> bool:
        package = patient_package.package if patient_package else None
        if not package:
            return False
        if package.program_id and program and package.program_id == program.id:
            return True

        package_names = [
            package.program.program_name if package.program else None,
            package.name,
        ]
        program_name = program.program_name if program else None
        normalized_program = AppointmentService._normalized_program_name(program_name)
        normalized_packages = [
            AppointmentService._normalized_program_name(name)
            for name in package_names
            if AppointmentService._normalized_program_name(name)
        ]
        if normalized_program and any(
            normalized_program == package_name
            or normalized_program in package_name
            or package_name in normalized_program
            for package_name in normalized_packages
        ):
            return True

        return any(
            AppointmentService._program_type(name) == program_type
            for name in package_names
            if name
        )

    @staticmethod
    def _program_capacity(program_type: str) -> int | None:
        return {
            "vocational": 10,
            "social": 8,
            "parent_training": 10,
            "peg": 10,
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
            "social": 60,
            "parent_training": 60,
            "peg": 60,
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
        therapist = db.query(Therapist).filter(Therapist.id == therapist_id).first()
        therapist_name = (therapist.name if therapist else None) or f"Therapist #{therapist_id}"
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
                raise ValueError(f"Therapist {therapist_name} already has an appointment in this time window: {detail}")

    @staticmethod
    def _validate_patient_window_available(
        db: Session,
        *,
        patient_id: int,
        slot_date,
        start_time,
        duration_minutes: int,
        program_id: int | None = None,
        program_type: str | None = None,
    ) -> None:
        new_start = AppointmentService._time_value_to_minutes(start_time)
        new_end = new_start + duration_minutes
        patient = db.query(Patient).filter(Patient.id == patient_id).first()
        child_name = f"{patient.first_name or ''} {patient.last_name or ''}".strip() if patient else None
        child_name = child_name or f"Child #{patient_id}"
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
                same_group_program = (
                    program_id
                    and getattr(existing_program, "id", None) == program_id
                    and program_type in {"vocational", "social", "parent_training", "peg"}
                    and existing_type == program_type
                    and existing_start == new_start
                    and existing_end == new_end
                )
                if same_group_program:
                    continue
                raise ValueError(
                    f"Child {child_name} already has an appointment in this time window: "
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
                    getattr(row, "crt_program_booking_id", None),
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

        if not patients:
            patient_query = db.query(Patient).filter(
                Patient.is_available == True,
                Patient.status == True,
                Patient.region_id.in_(current_user.region_ids),
            )
            if region_id:
                patient_query = patient_query.filter(Patient.region_id == region_id)
            patients = (
                patient_query
                .order_by(Patient.first_name.asc(), Patient.last_name.asc(), Patient.id.asc())
                .all()
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
    def book_slot(db: Session, booking_create: SlotBookingCreate, commit: bool = True):
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

        if AppointmentService._therapist_unavailable_for_slot(
            db,
            booking_create.therapist_id,
            booking_create.slot_date,
            slot.start_time,
        ):
            raise ValueError("Therapist is not available")

        booking_duration = int(booking_create.duration_minutes or AppointmentService._booking_duration_for_program(db, booking_create.program_id))
        if AppointmentService._booking_overlaps_break(slot, booking_duration):
            raise ValueError("Appointments cannot be booked during the 1:30 PM to 2:00 PM break")
        program = db.query(Program).filter(Program.id == booking_create.program_id).first() if booking_create.program_id else None
        if booking_create.program_id and not program:
            raise ValueError("Selected program is not available")
        if program and getattr(program, "region_id", None) and program.region_id != booking_create.region_id:
            raise ValueError("Selected program is not available for this region")
        program_type = AppointmentService._program_type(program.program_name if program else None)
        group_program = program_type in {"vocational", "social", "parent_training", "peg"}
        shared_slot_booking = bool(getattr(booking_create, "allow_shared_slot", False))
        existing_group_mapping = None
        if group_program or shared_slot_booking:
            existing_group_mapping = AppointmentRepository.get_active_therapist_slot_mapping(
                db,
                booking_create.therapist_id,
                booking_create.slot_id,
                booking_create.slot_date,
                booking_create.therapy_id,
            )
            if existing_group_mapping:
                active_count = (
                    db.query(PatientSlotBooking)
                    .filter(
                        PatientSlotBooking.therapist_slot_mapping_id == existing_group_mapping.id,
                        PatientSlotBooking.status_id.notin_([
                            AppointmentService.PATIENT_SLOT_BOOKING_UNPAID_CANCELLED_ID,
                            AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
                        ]),
                    )
                    .count()
                )
                capacity = AppointmentService._program_capacity_from_row(program, program_type) if group_program else 999
                if active_count >= capacity:
                    raise ValueError("Slot is already full")
                existing_patient_booking = (
                    db.query(PatientSlotBooking)
                    .filter(
                        PatientSlotBooking.therapist_slot_mapping_id == existing_group_mapping.id,
                        PatientSlotBooking.patient_id == booking_create.patient_id,
                        PatientSlotBooking.status_id.notin_([
                            AppointmentService.PATIENT_SLOT_BOOKING_UNPAID_CANCELLED_ID,
                            AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
                        ]),
                    )
                    .first()
                )
                if existing_patient_booking:
                    return {
                        "patient_slot_booking": existing_patient_booking,
                        "therapist_slot_mapping": existing_group_mapping,
                        "patient_session_plan_item": existing_patient_booking.patient_session_plan_item,
                    }
        if (not group_program and not shared_slot_booking) or not existing_group_mapping:
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
            program_id=booking_create.program_id,
            program_type=program_type,
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
            if not active_package or not AppointmentService._package_matches_program(active_package, program, program_type):
                raise ValueError("Selected package is not active for this child or does not match this program")
        elif booking_create.use_package or group_program:
            active_package = AppointmentService._active_patient_package(db, booking_create.patient_id, program_type)
        if not active_package and program_type in {"crt", "vocational"}:
            active_package = AppointmentService._get_or_create_auto_patient_package(
                db,
                patient_id=booking_create.patient_id,
                program=program,
                program_type=program_type,
                start_date=booking_create.slot_date,
            )
        is_package_session = active_package is not None
        package_total = float(active_package.total_amount or 0) if active_package else 0
        package_paid = float(active_package.paid_amount or 0) if active_package else 0
        package_fully_paid = bool(active_package and package_total > 0 and package_paid >= package_total)
        if active_package and active_package.package and active_package.package.total_sessions:
            amount_per_session = float(active_package.total_amount or active_package.package.price or 0) / float(active_package.package.total_sessions or 1)
        elif amount_per_session <= 0 and program:
            amount_per_session = float(program.per_session_amount or 0)
        booking_amount = amount_per_session if (program_type not in {"crt", "vocational", "social", "parent_training", "peg"} or bool(getattr(booking_create, "is_primary", True))) else 0
        crt_program_booking = None
        group_program_booking = None
        if group_program:
            group_program_booking = AppointmentService._get_or_create_group_program_booking(
                db,
                program_id=booking_create.program_id,
                region_id=booking_create.region_id,
                therapy_id=booking_create.therapy_id,
                slot_id=booking_create.slot_id,
                slot_date=booking_create.slot_date,
                start_time=slot.start_time,
                end_time=slot.end_time,
            )
        if program_type == "crt":
            if getattr(booking_create, "crt_program_booking_id", None):
                crt_program_booking = db.query(CrtProgramBooking).filter(
                    CrtProgramBooking.id == booking_create.crt_program_booking_id,
                    CrtProgramBooking.patient_id == booking_create.patient_id,
                ).first()
            if not crt_program_booking:
                crt_program_booking = (
                    db.query(CrtProgramBooking)
                    .filter(
                        CrtProgramBooking.patient_id == booking_create.patient_id,
                        CrtProgramBooking.program_id == booking_create.program_id,
                        CrtProgramBooking.slot_date == booking_create.slot_date,
                        CrtProgramBooking.slot_id == booking_create.slot_id,
                    )
                    .first()
                )
            if not crt_program_booking:
                crt_program_booking = CrtProgramBooking(
                    patient_id=booking_create.patient_id,
                    program_id=booking_create.program_id,
                    patient_package_id=active_package.id if active_package else None,
                    slot_id=booking_create.slot_id,
                    slot_date=booking_create.slot_date,
                    total_amount=amount_per_session,
                    paid_amount=0,
                    due_amount=max(0, amount_per_session),
                    is_package_session=is_package_session,
                    payment_status="UNPAID",
                    status_id=MASTER_LOOKUP_DATA["patient_slot_booking"]["BOOKED"],
                )
                db.add(crt_program_booking)
                db.flush()
            booking_amount = amount_per_session if bool(getattr(booking_create, "is_primary", True)) else 0
        booking_paid_amount = 0
        existing_package_usage = 0
        if active_package:
            billable_status_ids = [
                AppointmentService.PATIENT_SLOT_BOOKING_COMPLETED_ID,
                AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
            ]
            existing_package_usage_rows = (
                db.query(PatientSlotBooking)
                .filter(
                    PatientSlotBooking.patient_package_id == active_package.id,
                    PatientSlotBooking.is_package_session.is_(True),
                    PatientSlotBooking.status_id.in_(billable_status_ids),
                )
                .all()
            )
            emitted_usage_crt_ids: set[int] = set()
            for row in existing_package_usage_rows:
                if row.crt_program_booking_id:
                    crt_id = int(row.crt_program_booking_id)
                    if crt_id in emitted_usage_crt_ids:
                        continue
                    emitted_usage_crt_ids.add(crt_id)
                    crt_parent = row.crt_program_booking
                    amount = float((crt_parent.total_amount if crt_parent else None) or AppointmentService._slot_booking_amount(row) or 0)
                    if AppointmentService._slot_marked_fully_paid(db, row):
                        paid = float((crt_parent.paid_amount if crt_parent else getattr(row, "paid_amount", 0)) or 0)
                        covered = float((crt_parent.package_covered_amount if crt_parent else getattr(row, "package_covered_amount", 0)) or 0)
                        existing_package_usage += min(amount, paid + covered)
                    else:
                        existing_package_usage += amount
                    continue
                amount = AppointmentService._slot_booking_amount(row)
                if AppointmentService._slot_marked_fully_paid(db, row):
                    existing_package_usage += min(
                        amount,
                        float(getattr(row, "paid_amount", 0) or 0) + float(getattr(row, "package_covered_amount", 0) or 0),
                    )
                else:
                    existing_package_usage += amount
        available_package_credit = max(0, package_paid - existing_package_usage)
        booking_package_applied = min(available_package_credit, booking_amount) if is_package_session else 0
        booking_due_amount = max(0, booking_amount - booking_package_applied)
        booking_payment_status = "PACKAGE_COVERED" if is_package_session and booking_due_amount <= 0 else "UNPAID"
        if crt_program_booking and bool(getattr(booking_create, "is_primary", True)):
            crt_program_booking.total_amount = booking_amount
            crt_program_booking.package_covered_amount = booking_package_applied
            crt_program_booking.due_amount = booking_due_amount
            crt_program_booking.is_package_session = is_package_session
            crt_program_booking.patient_package_id = active_package.id if active_package else None
            crt_program_booking.payment_status = booking_payment_status

        therapist_slot_mapping = existing_group_mapping or AppointmentRepository.get_therapist_slot_mapping(
            db,
            therapist_id=booking_create.therapist_id,
            slot_id=booking_create.slot_id,
            slot_date=booking_create.slot_date,
        )
        if therapist_slot_mapping:
            therapist_slot_mapping.status_id = MASTER_LOOKUP_DATA["therapist_slot_mapping"]["BOOKED"]
            therapist_slot_mapping.therapy_id = booking_create.therapy_id
            db.flush()
        else:
            therapist_slot_mapping = AppointmentRepository.create_therapist_slot_mapping(
                db,
                therapist_id=booking_create.therapist_id,
                slot_id=booking_create.slot_id,
                slot_date=booking_create.slot_date,
                therapy_id=booking_create.therapy_id,
            )

        patient_slot_booking = AppointmentRepository.get_patient_slot_booking_for_mapping_child(
            db,
            therapist_slot_mapping_id=therapist_slot_mapping.id,
            patient_id=booking_create.patient_id,
        )
        if patient_slot_booking:
            if patient_slot_booking.status not in {"UNPAID_CANCELLED", "PAID_CANCELLED"}:
                raise ValueError("Child already has an active booking for this slot")
            patient_slot_booking.patient_session_plan_item_id = plan_item.id if plan_item else None
            patient_slot_booking.patient_package_id = active_package.id if active_package else None
            patient_slot_booking.crt_program_booking_id = crt_program_booking.id if crt_program_booking else None
            patient_slot_booking.group_program_booking_id = group_program_booking.id if group_program_booking else None
            patient_slot_booking.program_id = booking_create.program_id
            patient_slot_booking.duration_minutes = booking_duration
            patient_slot_booking.is_package_session = is_package_session
            patient_slot_booking.amount = booking_amount
            patient_slot_booking.paid_amount = booking_paid_amount
            patient_slot_booking.due_amount = booking_due_amount
            patient_slot_booking.fully_paid = False
            patient_slot_booking.payment_status = booking_payment_status
            patient_slot_booking.status_id = MASTER_LOOKUP_DATA["patient_slot_booking"]["BOOKED"]
            db.flush()
        else:
            patient_slot_booking = AppointmentRepository.create_patient_slot_booking(
                db,
                patient_id=booking_create.patient_id,
                therapist_slot_mapping_id=therapist_slot_mapping.id,
                patient_session_plan_item_id=plan_item.id if plan_item else None,
                patient_package_id=active_package.id if active_package else None,
                crt_program_booking_id=crt_program_booking.id if crt_program_booking else None,
                group_program_booking_id=group_program_booking.id if group_program_booking else None,
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
        if commit:
            db.commit()
            db.refresh(patient_slot_booking)
            db.refresh(therapist_slot_mapping)
            if plan_item:
                db.refresh(plan_item)
            if active_package:
                db.refresh(active_package)
        else:
            db.flush()

        return {
            "patient_slot_booking": patient_slot_booking,
            "therapist_slot_mapping": therapist_slot_mapping,
            "patient_session_plan_item": plan_item,
        }

    @staticmethod
    def _group_calendar_slots(slots: list[dict]) -> list[dict]:
        grouped: dict[tuple, list[dict]] = defaultdict(list)
        payment_state_by_group_child: dict[tuple, dict] = {}
        for slot in slots:
            group_id = slot.get("group_program_booking_id")
            patient_id = slot.get("patient_id")
            if not group_id or not patient_id:
                continue
            key = (group_id, patient_id)
            current = payment_state_by_group_child.get(key)
            slot_amount = float(slot.get("amount") or 0)
            slot_due = float(slot.get("due_amount") or 0)
            slot_paid = float(slot.get("paid_amount") or 0) + float(slot.get("package_covered_amount") or 0)
            current_due = float(slot.get("current_due_amount", slot_due) or 0)
            if current is None:
                payment_state_by_group_child[key] = {
                    "amount": slot_amount,
                    "paid_amount": slot_paid,
                    "due_amount": max(0, slot_amount - slot_paid),
                    "current_due_amount": current_due,
                    "current_fully_paid": False,
                    "payment_status": slot.get("payment_status"),
                    "fully_paid": False,
                    "patient_slot_booking_id": slot.get("patient_slot_booking_id"),
                    "patient_package_id": slot.get("patient_package_id"),
                    "is_package_session": slot.get("is_package_session"),
                    "package_name": slot.get("package_name"),
                }
                current = payment_state_by_group_child[key]
            else:
                current["amount"] = max(float(current.get("amount") or 0), slot_amount)
                current["paid_amount"] = float(current.get("paid_amount") or 0) + slot_paid
                current["due_amount"] = max(0, float(current.get("amount") or 0) - float(current.get("paid_amount") or 0))
                current["current_due_amount"] = min(float(current.get("current_due_amount") or 0), current_due)
                if not current.get("patient_slot_booking_id") and slot.get("patient_slot_booking_id"):
                    current["patient_slot_booking_id"] = slot.get("patient_slot_booking_id")
                current["patient_package_id"] = current.get("patient_package_id") or slot.get("patient_package_id")
                current["is_package_session"] = current.get("is_package_session") or slot.get("is_package_session")
                current["package_name"] = current.get("package_name") or slot.get("package_name")

            current_amount = float(current.get("amount") or 0)
            current_paid = float(current.get("paid_amount") or 0)
            fully_paid = current_amount > 0 and current_paid >= current_amount
            current["due_amount"] = max(0, current_amount - current_paid)
            current["current_due_amount"] = current["due_amount"]
            current["current_fully_paid"] = fully_paid
            current["fully_paid"] = fully_paid
            current["payment_status"] = "PAID" if fully_paid else "UNPAID"
        passthrough = []
        for slot in slots:
            if slot.get("patient_slot_booking_id"):
                grouped[(
                    slot.get("group_program_booking_id") or slot.get("slot_mapping_id"),
                    slot.get("slot_date"),
                    slot.get("slot_id"),
                    slot.get("therapistId") or slot.get("therapist_id"),
                    slot.get("therapy_id"),
                    slot.get("program_id"),
                )].append(slot)
            else:
                passthrough.append(slot)

        for rows in grouped.values():
            if len(rows) == 1 and rows[0].get("program_type") not in {"vocational", "social", "parent_training", "peg"}:
                passthrough.append(rows[0])
                continue
            primary = rows[0]
            participants = []
            for row in rows:
                if not row.get("patient_id"):
                    continue
                payment_state = payment_state_by_group_child.get(
                    (row.get("group_program_booking_id"), row.get("patient_id")),
                    {},
                )
                participants.append({
                    "id": row.get("patient_id"),
                    "full_name": row.get("patient_name"),
                    "patient_code": row.get("patient_code"),
                    "patient_slot_booking_id": payment_state.get("patient_slot_booking_id") or row.get("patient_slot_booking_id"),
                    "group_program_booking_id": row.get("group_program_booking_id"),
                    "patient_session_plan_id": row.get("patient_session_plan_id"),
                    "patient_phone": row.get("patient_phone"),
                    "amount": payment_state.get("amount", row.get("amount") or row.get("amount_per_session") or 0),
                    "paid_amount": payment_state.get("paid_amount", row.get("paid_amount") or 0),
                    "due_amount": payment_state.get("due_amount", row.get("due_amount") or 0),
                    "current_due_amount": payment_state.get("current_due_amount", row.get("current_due_amount", row.get("due_amount") or 0)),
                    "current_fully_paid": payment_state.get("current_fully_paid", row.get("current_fully_paid", False)),
                    "child_session_due_amount": row.get("child_session_due_amount", 0),
                    "child_package_due_amount": row.get("child_package_due_amount", 0),
                    "child_assessment_due_amount": row.get("child_assessment_due_amount", 0),
                    "fully_paid": payment_state.get("fully_paid", bool(row.get("current_fully_paid")) or bool(str(row.get("payment_status") or "").upper() in {"PAID", "PACKAGE_COVERED"} and float(row.get("current_due_amount", row.get("due_amount") or 0) or 0) <= 0)),
                    "payment_status": payment_state.get("payment_status", row.get("payment_status")),
                    "status": row.get("patient_slot_booking_status") or row.get("status"),
                    "patient_package_id": payment_state.get("patient_package_id", row.get("patient_package_id")),
                    "is_package_session": payment_state.get("is_package_session", row.get("is_package_session")),
                    "package_name": payment_state.get("package_name", row.get("package_name")),
                })
            primary["participants"] = participants
            primary["child_count"] = len(participants)
            passthrough.append(primary)

        return passthrough

    @staticmethod
    def _attach_crt_therapist_slots(therapist_map) -> None:
        crt_groups: dict[tuple, list[dict]] = defaultdict(list)

        for therapist in therapist_map.values():
            for slot in therapist["slots"]:
                if slot.get("program_type") != "crt" or not slot.get("patient_slot_booking_id"):
                    continue
                key = (
                    slot.get("crt_program_booking_id"),
                    slot.get("patient_id"),
                    slot.get("program_id"),
                    slot.get("slot_date"),
                )
                crt_groups[key].append(slot)

        for rows in crt_groups.values():
            therapist_slots = [
                {
                    "id": row.get("slot_mapping_id") or row.get("patient_slot_booking_id"),
                    "therapist_id": row.get("therapist_id"),
                    "therapist_name": row.get("therapist_name"),
                    "therapy_id": row.get("therapy_id"),
                    "therapy_name": row.get("therapy_name"),
                    "start_time": row.get("start_time"),
                    "end_time": row.get("end_time"),
                    "duration_minutes": row.get("duration_minutes"),
                }
                for row in sorted(rows, key=lambda item: ((item.get("start_time") or ""), int(item.get("slot_mapping_id") or 0)))
            ]
            for row in rows:
                row["therapist_slots"] = therapist_slots

    @staticmethod
    def _group_crt_calendar_slots(slots: list[dict]) -> list[dict]:
        grouped: dict[tuple, list[dict]] = defaultdict(list)
        passthrough = []

        for slot in slots:
            if slot.get("program_type") != "crt" or not slot.get("crt_program_booking_id"):
                passthrough.append(slot)
                continue
            grouped[(
                slot.get("crt_program_booking_id"),
                slot.get("program_id"),
                slot.get("slot_date"),
            )].append(slot)

        for rows in grouped.values():
            participants_by_patient: dict[int, dict] = {}

            for row in rows:
                for participant in row.get("participants") or []:
                    patient_id = participant.get("id")
                    if not patient_id:
                        continue
                    current = participants_by_patient.get(patient_id)
                    participant_due = float(participant.get("due_amount") or 0)
                    current_due = float(current.get("due_amount") or 0) if current else -1
                    if current is None or participant_due >= current_due:
                        participants_by_patient[patient_id] = participant

            therapist_slots_by_key = {}
            for row in rows:
                for therapist_slot in row.get("therapist_slots") or []:
                    key = (
                        therapist_slot.get("id"),
                        therapist_slot.get("therapist_id"),
                        therapist_slot.get("therapy_id"),
                        therapist_slot.get("start_time"),
                        therapist_slot.get("end_time"),
                    )
                    therapist_slots_by_key[key] = therapist_slot
            therapist_slots = (
                sorted(
                    therapist_slots_by_key.values(),
                    key=lambda item: ((item.get("start_time") or ""), int(item.get("id") or 0)),
                )
                if therapist_slots_by_key
                else None
            )

            participants = list(participants_by_patient.values())
            for row in rows:
                row["participants"] = participants
                row["child_count"] = len(participants)
                if therapist_slots is not None:
                    row["therapist_slots"] = therapist_slots
                passthrough.append(row)

        return passthrough

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
        AppointmentService._cancel_related_crt_segments(
            db,
            patient_slot_booking,
            target_status="UNPAID_CANCELLED",
        )
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
    def _cancel_related_crt_segments(
        db: Session,
        patient_slot_booking: PatientSlotBooking,
        *,
        target_status: str,
        exclude_current: bool = True,
    ) -> None:
        mapping = patient_slot_booking.therapist_slot_mapping
        if not mapping or not patient_slot_booking.patient_id or not patient_slot_booking.program_id:
            return
        program_type = AppointmentService._program_type(patient_slot_booking.program.program_name if patient_slot_booking.program else None)
        if program_type != "crt":
            return

        siblings = (
            db.query(PatientSlotBooking)
            .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
            .filter(
                PatientSlotBooking.patient_id == patient_slot_booking.patient_id,
                PatientSlotBooking.program_id == patient_slot_booking.program_id,
                TherapistSlotMapping.slot_date == mapping.slot_date,
                PatientSlotBooking.status_id.notin_([
                    AppointmentService.PATIENT_SLOT_BOOKING_UNPAID_CANCELLED_ID,
                    AppointmentService.PATIENT_SLOT_BOOKING_PAID_CANCELLED_ID,
                ]),
                TherapistSlotMapping.status_id != AppointmentService.THERAPIST_SLOT_MAPPING_CANCELLED_ID,
            )
            .all()
        )
        for sibling in siblings:
            if exclude_current and sibling.id == patient_slot_booking.id:
                continue
            sibling.status = target_status
            sibling_mapping = sibling.therapist_slot_mapping
            if sibling_mapping and not AppointmentRepository.has_active_patient_booking_for_mapping(
                db,
                sibling_mapping.id,
                exclude_patient_slot_booking_id=sibling.id,
            ):
                sibling_mapping.status = "CANCELLED"

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
        if not plan_item:
            plan_item = AppointmentRepository.get_plan_item_for_booking(
                db,
                patient_slot_booking.patient_id,
                booking_create.patient_session_plan_id,
                booking_create.therapy_id,
            )
            if plan_item:
                patient_slot_booking.patient_session_plan_item_id = plan_item.id
                db.flush()
        if not current_mapping or not current_mapping.slot:
            raise ValueError("Slot booking details are incomplete")

        slot = AppointmentRepository.get_slot(db, booking_create.slot_id)
        if not slot:
            raise ValueError("Selected slot is not available")
        booking_duration = int(
            booking_create.duration_minutes
            or getattr(patient_slot_booking, "duration_minutes", None)
            or AppointmentService._booking_duration_for_program(db, booking_create.program_id or patient_slot_booking.program_id)
        )
        if AppointmentService._booking_overlaps_break(slot, booking_duration):
            raise ValueError("Appointments cannot be booked during the 1:30 PM to 2:00 PM break")

        if AppointmentService._therapist_unavailable_for_slot(
            db,
            booking_create.therapist_id,
            booking_create.slot_date,
            slot.start_time,
        ):
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
        if plan_item:
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
    def _therapist_unavailable_for_slot(db: Session, therapist_id: int, slot_date, slot_start) -> bool:
        therapist_leave = AppointmentRepository.get_therapist_leave(
            db,
            therapist_id,
            slot_date,
        )
        return AppointmentService._leave_blocks_slot(therapist_leave, slot_start)
    
    @staticmethod
    def get_calendar_view(
        db,
        selected_date,
        region_ids
    ):
        if not AppointmentService._has_table(db, "patient_slot_booking"):
            therapists = AppointmentService._legacy_calendar_view(db, selected_date, selected_date, region_ids)
            return {
                "date": str(selected_date),
                "therapists": therapists,
                "total": len(therapists),
            }

        AppointmentService._ensure_package_billing_schema(db)
        records = AppointmentRepository.get_calendar_data(
            db=db,
            selected_date=selected_date,
            region_ids=region_ids
        )
        due_buckets_by_patient = AppointmentService._patient_due_buckets(
            db,
            {int(row.patient_id) for row in records if row.patient_id},
        )

        leaves = AppointmentRepository.get_therapist_leaves(
            db=db,
            selected_date=selected_date
        )

        leaves_by_therapist = {leave.therapist_id: leave for leave in leaves}

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
            due_buckets = due_buckets_by_patient.get(int(patient_id), {}) if patient_id else {}
            child_session_due = float(due_buckets.get("session_due_amount", 0) or 0)
            child_package_due = float(due_buckets.get("package_due_amount", 0) or 0)
            child_assessment_due = float(due_buckets.get("assessment_due_amount", 0) or 0)
            is_crt_booking = bool(row.crt_program_booking_id)
            slot_amount = float((row.crt_total_amount if is_crt_booking else row.amount) or (row.program_per_session_amount if program_type == "crt" and row.patient_slot_booking_id else 0) or 0)
            slot_paid_amount = float((row.crt_paid_amount if is_crt_booking else row.paid_amount) or 0)
            slot_due_amount = float((row.crt_due_amount if is_crt_booking else row.due_amount) or 0)
            slot_package_covered_amount = float((row.crt_package_covered_amount if is_crt_booking else getattr(row, "package_covered_amount", 0)) or 0)
            current_payment_state = AppointmentService._slot_current_payment_state(
                db,
                row.patient_slot_booking_id,
                row.crt_program_booking_id,
                slot_amount,
                slot_due_amount,
                row.crt_payment_status if is_crt_booking else row.payment_status,
                bool(row.is_package_session) if not is_crt_booking else False,
                slot_package_covered_amount,
            )
            if is_crt_booking and bool(getattr(row, "crt_fully_paid", False)):
                current_payment_state = {
                    "current_due_amount": 0.0,
                    "current_fully_paid": True,
                    "current_paid_amount": slot_paid_amount,
                }
            display_paid_amount = max(
                slot_paid_amount,
                float(current_payment_state.get("current_paid_amount") or 0),
            )

            therapist_map[therapist_id]["slots"].append({
                "slot_mapping_id": row.slot_mapping_id,
                "therapist_id": therapist_id,
                "therapist_name": therapist_name,
                "patient_slot_booking_id": row.patient_slot_booking_id,
                "crt_program_booking_id": row.crt_program_booking_id,
                "crt_fully_paid": bool(getattr(row, "crt_fully_paid", False)) if is_crt_booking else False,
                "crt_payment_status": row.crt_payment_status if is_crt_booking else None,
                "group_program_booking_id": row.group_program_booking_id,
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
                "amount_per_session": float(row.amount_per_session or row.program_per_session_amount or 0),
                "patient_package_id": row.patient_package_id,
                "is_package_session": bool(row.is_package_session),
                "package_name": row.package_name,
                "package_total_amount": float(row.package_total_amount or 0),
                "package_paid_amount": float(row.package_paid_amount or 0),
                "package_due_amount": float(row.package_due_amount or 0),
                "child_session_due_amount": child_session_due,
                "child_package_due_amount": child_package_due,
                "child_assessment_due_amount": child_assessment_due,
                "package_payment_status": row.package_payment_status,
                "amount": slot_amount,
                "paid_amount": display_paid_amount,
                "due_amount": slot_due_amount,
                "package_covered_amount": slot_package_covered_amount,
                "current_due_amount": current_payment_state["current_due_amount"],
                "current_fully_paid": current_payment_state["current_fully_paid"],
                "is_fully_paid": bool(getattr(row, "fully_paid", False)),
                "fully_paid": bool(getattr(row, "fully_paid", False)) if not is_crt_booking else bool(current_payment_state["current_fully_paid"]),
                "payment_status": row.crt_payment_status if is_crt_booking else row.payment_status,
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
                    "patient_slot_booking_id": row.patient_slot_booking_id,
                    "crt_program_booking_id": row.crt_program_booking_id,
                    "crt_fully_paid": bool(getattr(row, "crt_fully_paid", False)) if is_crt_booking else False,
                    "crt_payment_status": row.crt_payment_status if is_crt_booking else None,
                    "group_program_booking_id": row.group_program_booking_id,
                    "patient_session_plan_id": row.patient_session_plan_id,
                    "patient_phone": patient_phone,
                    "amount": slot_amount,
                    "paid_amount": display_paid_amount,
                    "due_amount": slot_due_amount,
                    "package_covered_amount": slot_package_covered_amount,
                    "current_due_amount": current_payment_state["current_due_amount"],
                    "current_fully_paid": current_payment_state["current_fully_paid"],
                    "is_fully_paid": bool(getattr(row, "fully_paid", False)),
                    "child_session_due_amount": child_session_due,
                    "child_package_due_amount": child_package_due,
                    "child_assessment_due_amount": child_assessment_due,
                    "fully_paid": bool(getattr(row, "fully_paid", False)) if not is_crt_booking else bool(current_payment_state["current_fully_paid"]),
                    "payment_status": row.crt_payment_status if is_crt_booking else row.payment_status,
                    "status": STATUS_ID_TO_CODE["patient_slot_booking"].get(row.patient_slot_booking_status_id),
                    "patient_package_id": row.patient_package_id,
                    "is_package_session": bool(row.is_package_session),
                    "package_name": row.package_name,
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

        AppointmentService._attach_crt_therapist_slots(therapist_map)

        for therapist in therapist_map.values():
            therapist["slots"] = AppointmentService._group_crt_calendar_slots(therapist["slots"])
            therapist["slots"] = AppointmentService._group_calendar_slots(therapist["slots"])
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
        if not AppointmentService._has_table(db, "patient_slot_booking"):
            therapists = AppointmentService._legacy_calendar_view(db, start_date, end_date, region_ids)
            return {
                "start_date": str(start_date),
                "end_date": str(end_date),
                "therapists": therapists,
                "total": len(therapists),
            }

        AppointmentService._ensure_package_billing_schema(db)
        records = AppointmentRepository.get_calendar_data_range(
            db=db,
            start_date=start_date,
            end_date=end_date,
            region_ids=region_ids
        )
        due_buckets_by_patient = AppointmentService._patient_due_buckets(
            db,
            {int(row.patient_id) for row in records if row.patient_id},
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
            due_buckets = due_buckets_by_patient.get(int(patient_id), {}) if patient_id else {}
            child_session_due = float(due_buckets.get("session_due_amount", 0) or 0)
            child_package_due = float(due_buckets.get("package_due_amount", 0) or 0)
            child_assessment_due = float(due_buckets.get("assessment_due_amount", 0) or 0)
            is_crt_booking = bool(row.crt_program_booking_id)
            slot_amount = float((row.crt_total_amount if is_crt_booking else row.amount) or (row.program_per_session_amount if program_type == "crt" and row.patient_slot_booking_id else 0) or 0)
            slot_paid_amount = float((row.crt_paid_amount if is_crt_booking else row.paid_amount) or 0)
            slot_due_amount = float((row.crt_due_amount if is_crt_booking else row.due_amount) or 0)
            slot_package_covered_amount = float((row.crt_package_covered_amount if is_crt_booking else getattr(row, "package_covered_amount", 0)) or 0)
            current_payment_state = AppointmentService._slot_current_payment_state(
                db,
                row.patient_slot_booking_id,
                row.crt_program_booking_id,
                slot_amount,
                slot_due_amount,
                row.crt_payment_status if is_crt_booking else row.payment_status,
                bool(row.is_package_session) if not is_crt_booking else False,
                slot_package_covered_amount,
            )
            if is_crt_booking and bool(getattr(row, "crt_fully_paid", False)):
                current_payment_state = {
                    "current_due_amount": 0.0,
                    "current_fully_paid": True,
                    "current_paid_amount": slot_paid_amount,
                }
            display_paid_amount = max(
                slot_paid_amount,
                float(current_payment_state.get("current_paid_amount") or 0),
            )

            therapist_map[therapist_id]["slots"].append({
                "slot_mapping_id": row.slot_mapping_id,
                "therapist_id": therapist_id,
                "therapist_name": row.therapist_name,
                "patient_slot_booking_id": row.patient_slot_booking_id,
                "crt_program_booking_id": row.crt_program_booking_id,
                "crt_fully_paid": bool(getattr(row, "crt_fully_paid", False)) if is_crt_booking else False,
                "crt_payment_status": row.crt_payment_status if is_crt_booking else None,
                "group_program_booking_id": row.group_program_booking_id,
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
                "amount_per_session": float(row.amount_per_session or row.program_per_session_amount or 0),
                "patient_package_id": row.patient_package_id,
                "is_package_session": bool(row.is_package_session),
                "package_name": row.package_name,
                "package_total_amount": float(row.package_total_amount or 0),
                "package_paid_amount": float(row.package_paid_amount or 0),
                "package_due_amount": float(row.package_due_amount or 0),
                "child_session_due_amount": child_session_due,
                "child_package_due_amount": child_package_due,
                "child_assessment_due_amount": child_assessment_due,
                "package_payment_status": row.package_payment_status,
                "amount": slot_amount,
                "paid_amount": display_paid_amount,
                "due_amount": slot_due_amount,
                "package_covered_amount": slot_package_covered_amount,
                "current_due_amount": current_payment_state["current_due_amount"],
                "current_fully_paid": current_payment_state["current_fully_paid"],
                "is_fully_paid": bool(getattr(row, "fully_paid", False)),
                "fully_paid": bool(getattr(row, "fully_paid", False)) if not is_crt_booking else bool(current_payment_state["current_fully_paid"]),
                "payment_status": row.crt_payment_status if is_crt_booking else row.payment_status,
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
                    "patient_slot_booking_id": row.patient_slot_booking_id,
                    "crt_program_booking_id": row.crt_program_booking_id,
                    "crt_fully_paid": bool(getattr(row, "crt_fully_paid", False)) if is_crt_booking else False,
                    "crt_payment_status": row.crt_payment_status if is_crt_booking else None,
                    "group_program_booking_id": row.group_program_booking_id,
                    "patient_session_plan_id": row.patient_session_plan_id,
                    "patient_phone": patient_phone,
                    "amount": slot_amount,
                    "paid_amount": display_paid_amount,
                    "due_amount": slot_due_amount,
                    "package_covered_amount": slot_package_covered_amount,
                    "current_due_amount": current_payment_state["current_due_amount"],
                    "current_fully_paid": current_payment_state["current_fully_paid"],
                    "is_fully_paid": bool(getattr(row, "fully_paid", False)),
                    "child_session_due_amount": child_session_due,
                    "child_package_due_amount": child_package_due,
                    "child_assessment_due_amount": child_assessment_due,
                    "fully_paid": bool(getattr(row, "fully_paid", False)) if not is_crt_booking else bool(current_payment_state["current_fully_paid"]),
                    "payment_status": row.crt_payment_status if is_crt_booking else row.payment_status,
                    "status": STATUS_ID_TO_CODE["patient_slot_booking"].get(row.patient_slot_booking_status_id),
                    "patient_package_id": row.patient_package_id,
                    "is_package_session": bool(row.is_package_session),
                    "package_name": row.package_name,
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

        AppointmentService._attach_crt_therapist_slots(therapist_map)

        for therapist in therapist_map.values():
            therapist["slots"] = AppointmentService._group_crt_calendar_slots(therapist["slots"])
            therapist["slots"] = AppointmentService._group_calendar_slots(therapist["slots"])
            therapist["slots"].sort(key=lambda slot: (slot["slot_date"], slot["start_time"]))

        return {
            "start_date": str(start_date),
            "end_date": str(end_date),
            "therapists": list(therapist_map.values())
        }

    @staticmethod
    def _linked_program_slot_siblings(db: Session, slot_booking: PatientSlotBooking) -> list[PatientSlotBooking]:
        """Rows for the same child/program session across multiple therapists."""
        if not slot_booking.patient_id:
            return []
        if slot_booking.crt_program_booking_id:
            return []
        if slot_booking.group_program_booking_id:
            return (
                db.query(PatientSlotBooking)
                .filter(
                    PatientSlotBooking.id != slot_booking.id,
                    PatientSlotBooking.patient_id == slot_booking.patient_id,
                    PatientSlotBooking.group_program_booking_id == slot_booking.group_program_booking_id,
                )
                .all()
            )

        mapping = slot_booking.therapist_slot_mapping
        if not mapping or not slot_booking.program_id:
            return []

        return (
            db.query(PatientSlotBooking)
            .join(TherapistSlotMapping, TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id)
            .filter(
                PatientSlotBooking.id != slot_booking.id,
                PatientSlotBooking.patient_id == slot_booking.patient_id,
                PatientSlotBooking.program_id == slot_booking.program_id,
                TherapistSlotMapping.slot_date == mapping.slot_date,
                TherapistSlotMapping.slot_id == mapping.slot_id,
                PatientSlotBooking.crt_program_booking_id.is_(None),
            )
            .all()
        )

    @staticmethod
    def _mirror_linked_program_slot_payment_state(
        source: PatientSlotBooking,
        siblings: list[PatientSlotBooking],
    ) -> None:
        for sibling in siblings:
            sibling.fully_paid = bool(source.fully_paid)
            sibling.payment_status = source.payment_status
            sibling.patient_package_id = source.patient_package_id
            sibling.is_package_session = bool(source.is_package_session)
            sibling.package_covered_amount = 0
            sibling.paid_amount = 0
            sibling.due_amount = 0
    
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

        sibling_slot_bookings = AppointmentService._linked_program_slot_siblings(db, patient_slot_booking)
        current_status = str(patient_slot_booking.status or "").upper()
        therapist_slot_mapping = patient_slot_booking.therapist_slot_mapping
        plan_item = patient_slot_booking.patient_session_plan_item
        package = patient_slot_booking.patient_package
        same_package_completed_sibling = any(
            sibling.patient_package_id == patient_slot_booking.patient_package_id
            and bool(sibling.is_package_session)
            and str(sibling.status or "").upper() == "COMPLETED"
            for sibling in sibling_slot_bookings
        )

        if current_status in {"UNPAID_CANCELLED", "PAID_CANCELLED"}:
            raise ValueError("Slot booking already cancelled")

        if normalized_action == "complete" and current_status == "COMPLETED":
            raise ValueError("Slot booking already completed")

        if normalized_action == "complete":
            patient_slot_booking.status = "COMPLETED"
            if therapist_slot_mapping:
                therapist_slot_mapping.status = "COMPLETED"
            for sibling in sibling_slot_bookings:
                sibling.status = "COMPLETED"
                if sibling.therapist_slot_mapping:
                    sibling.therapist_slot_mapping.status = "COMPLETED"
            if plan_item:
                assigned_sessions = plan_item.assigned_sessions or 0
                completed_sessions = plan_item.completed_sessions or 0
                if assigned_sessions > 0:
                    plan_item.assigned_sessions = assigned_sessions - 1
                plan_item.completed_sessions = completed_sessions + 1
            if package and patient_slot_booking.is_package_session and not same_package_completed_sibling:
                package.sessions_completed = (package.sessions_completed or 0) + 1
                package.sessions_remaining = max(0, (package.sessions_remaining or 0) - 1)
                AppointmentService._sync_patient_package_completion_status(package)
        else:
            normalized_cancel_type = str(cancel_type or "unpaid").strip().lower()
            patient_slot_booking.status = "PAID_CANCELLED" if normalized_cancel_type == "paid" else "UNPAID_CANCELLED"
            for sibling in sibling_slot_bookings:
                sibling.status = patient_slot_booking.status
            AppointmentService._cancel_related_crt_segments(
                db,
                patient_slot_booking,
                target_status=patient_slot_booking.status,
            )
            remaining_completed_package_sibling = any(
                sibling.patient_package_id == patient_slot_booking.patient_package_id
                and bool(sibling.is_package_session)
                and str(sibling.status or "").upper() == "COMPLETED"
                for sibling in sibling_slot_bookings
            )
            if package and patient_slot_booking.is_package_session and current_status == "COMPLETED" and not remaining_completed_package_sibling:
                package.sessions_completed = max(0, (package.sessions_completed or 0) - 1)
                package.sessions_remaining = (package.sessions_remaining or 0) + 1
                AppointmentService._sync_patient_package_completion_status(package)
            if therapist_slot_mapping and not AppointmentRepository.has_active_patient_booking_for_mapping(
                db,
                therapist_slot_mapping.id,
                exclude_patient_slot_booking_id=patient_slot_booking.id,
            ):
                therapist_slot_mapping.status = "CANCELLED"
            for sibling in sibling_slot_bookings:
                sibling_mapping = sibling.therapist_slot_mapping
                if sibling_mapping and not AppointmentRepository.has_active_patient_booking_for_mapping(
                    db,
                    sibling_mapping.id,
                    exclude_patient_slot_booking_id=sibling.id,
                ):
                    sibling_mapping.status = "CANCELLED"
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
        if normalized_action == "complete" and not patient_slot_booking.is_package_session:
            package_with_credit = AppointmentService._package_with_credit_for_slot(db, patient_slot_booking)
            if package_with_credit:
                patient_slot_booking.patient_package_id = package_with_credit.id
                patient_slot_booking.is_package_session = True
                patient_slot_booking.patient_package = package_with_credit
                package = package_with_credit
                db.flush()
        package_total = float(package.total_amount or 0) if package else 0
        package_paid = float(package.paid_amount or 0) if package else 0
        package_fully_paid = bool(patient_slot_booking.is_package_session and package and package_total > 0 and package_paid >= package_total)
        paid_amount = float(patient_slot_booking.paid_amount or 0)
        is_paid_cancel = normalized_action == "cancel" and str(cancel_type or "").strip().lower() == "paid"

        if (
            bool(getattr(patient_slot_booking, "fully_paid", False))
            and not is_paid_cancel
            and not (normalized_action == "complete" and package and patient_slot_booking.is_package_session)
        ):
            patient_slot_booking.due_amount = 0
            patient_slot_booking.payment_status = "PACKAGE_COVERED" if patient_slot_booking.is_package_session else "PAID"
        elif package and patient_slot_booking.is_package_session:
            payable_slot = AppointmentService._crt_payable_slot(db, patient_slot_booking)
            payable_amount = AppointmentService._slot_booking_amount(payable_slot)
            if payable_amount <= 0 and patient_slot_booking.crt_program_booking_id:
                payable_amount = AppointmentService._slot_booking_amount(patient_slot_booking)
                payable_slot.amount = payable_amount
                db.flush()
            AppointmentService._recalculate_package_slot_payments(db, package)
            if normalized_action == "complete" or is_paid_cancel:
                latest_package_due = AppointmentService._latest_package_payment_due(db, package)
                if is_paid_cancel:
                    balance_after = max(0, float(payable_slot.due_amount or 0))
                else:
                    signed_due_after = AppointmentService._package_signed_due(db, package)
                    current_package_due = float(getattr(package, "due_amount", 0) or 0)
                    credit_before_candidates = [
                        value for value in (latest_package_due, current_package_due, signed_due_after - payable_amount)
                        if value is not None and float(value) < 0
                    ]
                    if credit_before_candidates:
                        balance_after = min(float(value) for value in credit_before_candidates) + payable_amount
                    else:
                        balance_after = signed_due_after
                if is_paid_cancel:
                    AppointmentService._record_slot_payment_if_missing(
                        db,
                        payable_slot,
                        amount=0,
                        due_amount=balance_after,
                        remark_text="Paid cancellation",
                    )
                elif normalized_action == "complete":
                    package_covered_amount = float(getattr(payable_slot, "package_covered_amount", 0) or 0)
                    if package_covered_amount > 0:
                        AppointmentService._record_slot_payment_if_missing(
                            db,
                            payable_slot,
                            amount=payable_amount,
                            due_amount=balance_after,
                            remark_text="Package credit used",
                        )
                    else:
                        AppointmentService._record_slot_payment_if_missing(
                            db,
                            payable_slot,
                            amount=0,
                            due_amount=balance_after,
                            remark_text="Session completed",
                        )
        else:
            patient_slot_booking.due_amount = max(0, slot_amount - paid_amount) if is_paid_cancel else (0 if package_fully_paid else max(0, slot_amount - paid_amount))
            if package_fully_paid and not is_paid_cancel:
                patient_slot_booking.payment_status = "PACKAGE_COVERED"
            elif paid_amount >= slot_amount and slot_amount > 0:
                patient_slot_booking.payment_status = "PAID"
            elif paid_amount > 0:
                patient_slot_booking.payment_status = "PARTIAL"
            else:
                patient_slot_booking.payment_status = "UNPAID"
            if normalized_action == "complete" and patient_slot_booking.due_amount > 0:
                payable_slot = AppointmentService._crt_payable_slot(db, patient_slot_booking)
                if payable_slot.id != patient_slot_booking.id and float(payable_slot.due_amount or 0) <= 0:
                    payable_slot.due_amount = float(patient_slot_booking.due_amount or 0)
                completion_due = (
                    AppointmentService._patient_normal_session_due(db, patient_slot_booking)
                    if not patient_slot_booking.is_package_session and not patient_slot_booking.crt_program_booking_id
                    else float(payable_slot.due_amount or patient_slot_booking.due_amount or 0)
                )
                AppointmentService._record_slot_payment_if_missing(
                    db,
                    payable_slot,
                    amount=0,
                    due_amount=completion_due,
                    remark_text="Session completed",
                )

        if is_paid_cancel:
            patient_slot_booking.fully_paid = False
            if not (package and patient_slot_booking.is_package_session):
                patient_slot_booking.paid_amount = float(patient_slot_booking.paid_amount or 0)
                patient_slot_booking.due_amount = max(0, slot_amount - float(patient_slot_booking.paid_amount or 0))
                patient_slot_booking.payment_status = "UNPAID" if patient_slot_booking.due_amount > 0 else "PAID"
            AppointmentService._record_slot_payment_if_missing(
                db,
                patient_slot_booking,
                amount=0,
                due_amount=float(patient_slot_booking.due_amount or 0),
                remark_text="Paid cancellation",
            )

        if package and patient_slot_booking.is_package_session:
            db.flush()
            AppointmentService._recalculate_package_slot_payments(db, package)
            payable_slot = AppointmentService._crt_payable_slot(db, patient_slot_booking)
            payment_filters = []
            if payable_slot.id:
                payment_filters.append(Payment.patient_slot_booking_id == payable_slot.id)
                payment_filters.append(Payment.remark.like(f"[slot:{payable_slot.id}]%"))
            if payable_slot.crt_program_booking_id:
                payment_filters.append(Payment.crt_program_booking_id == payable_slot.crt_program_booking_id)
            if payment_filters:
                latest_payment = (
                    db.query(Payment)
                    .filter(
                        Payment.patient_id == payable_slot.patient_id,
                        or_(*payment_filters),
                    )
                    .order_by(Payment.created_at.desc(), Payment.id.desc())
                    .first()
                )
                if latest_payment:
                    latest_due = float(package.due_amount or 0)
                    latest_payment.due_amount = latest_due
                    if "Marked as fully paid" not in str(getattr(latest_payment, "payment_note", "") or ""):
                        latest_payment.fully_paid = False
            AppointmentService._sync_patient_package_completion_status(package)

        AppointmentService._mirror_linked_program_slot_payment_state(
            patient_slot_booking,
            sibling_slot_bookings,
        )

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
    
    
