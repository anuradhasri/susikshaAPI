from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.models import (
    Appointment,
    Patient,
    PatientSessionPlan,
    PatientSessionPlanItem,
    PatientSlotBooking,
    SlotMaster,
    Therapist,
    TherapistLeave,
    TherapistSlotMapping,
    TherapistTherapyMapping,
    TherapyMaster,
)
from app.schemas.schemas import AppointmentCreate, AppointmentUpdate
from app.utils.query_utils import filter_by_region, soft_delete


class AppointmentRepository:
    """Database access for calendar appointments."""

    @staticmethod
    def get_patient_plans(
        db: Session,
        patient_id: int
    ):

        plans = (
            db.query(PatientSessionPlan)
            .filter(
                PatientSessionPlan.patient_id == patient_id,
                PatientSessionPlan.status_id == 401
            )
            .all()
        )

        response = [
            {
                "id": plan.id,
                "plan_name": plan.plan_name,
                "total_sessions": plan.total_sessions,
                "start_date": plan.start_date.isoformat() if plan.start_date else None,
                "end_date": plan.end_date.isoformat() if plan.end_date else None,
            }
            for plan in plans
        ]

        return {
            "total": len(response),
            "data": response
        }
    
    @staticmethod
    def get_patient_plans_therapies(
        db: Session,
        patient_session_plan_id: int
    ):

        therapies = (
            db.query(
                PatientSessionPlanItem.id,
                TherapyMaster.id.label("therapy_id"),
                TherapyMaster.name.label("therapy_name"),
                PatientSessionPlanItem.allocated_sessions,
                PatientSessionPlanItem.assigned_sessions,
                PatientSessionPlanItem.completed_sessions,
                PatientSessionPlanItem.amount_per_session,
            )
            .join(
                TherapyMaster,
                TherapyMaster.id == PatientSessionPlanItem.therapy_id
            )
            .filter(
                PatientSessionPlanItem.patient_session_plan_id == patient_session_plan_id,
                TherapyMaster.is_active == 1
            )
            .all()
        )

        response = [
            {
                "id": therapy.id,
                "therapy_id": therapy.therapy_id,
                "therapy_name": therapy.therapy_name,
                "allocated_sessions": therapy.allocated_sessions,
                "assigned_sessions": therapy.assigned_sessions or 0,
                "completed_sessions": therapy.completed_sessions or 0,
                "remaining_sessions": max(
                    0,
                    (therapy.allocated_sessions or 0)
                    - (therapy.assigned_sessions or 0)
                    - (therapy.completed_sessions or 0),
                ),
                "amount_per_session": float(therapy.amount_per_session or 0),
            }
            for therapy in therapies
        ]

        return {
            "total": len(response),
            "data": response
        }
    
    @staticmethod
    def get_therapists(
        db: Session,
        therapy_id: int,
        region_ids: Optional[list[int]] = None,
    ):

        query = (
            db.query(
                Therapist.id.label("therapist_id"),
                Therapist.name.label("therapist_name")
            )
            .join(
                TherapistTherapyMapping,
                TherapistTherapyMapping.therapist_id == Therapist.id
            )
            .filter(
                TherapistTherapyMapping.therapy_id == therapy_id,
                TherapistTherapyMapping.is_active == 1,
                Therapist.is_active ==1 
            )
        )
        therapists = query.all()

        if not therapists:
            fallback_query = (
                db.query(
                    Therapist.id.label("therapist_id"),
                    Therapist.name.label("therapist_name")
                )
                .filter(Therapist.is_active == 1)
            )
            therapists = fallback_query.order_by(Therapist.name.asc()).all()

        response = [
            {
                "therapist_id": therapist.therapist_id,
                "therapist_name": therapist.therapist_name
            }
            for therapist in therapists
        ]

        return {
            "total": len(response),
            "data": response
        }
        
    @staticmethod
    def create(db: Session, appointment_create: AppointmentCreate) -> Appointment:
        appointment = Appointment(**appointment_create.model_dump())
        db.add(appointment)
        db.flush()
        return appointment

    @staticmethod
    def get_by_id(db: Session, appointment_id: int, region_id: Optional[int] = None) -> Optional[Appointment]:
        query = db.query(Appointment).filter(
            Appointment.id == appointment_id,
            Appointment.deleted_at.is_(None),
        )
        if region_id:
            query = filter_by_region(query, region_id, Appointment)
        return query.first()

    @staticmethod
    def list(
        db: Session,
        *,
        region_id: Optional[int] = None,
        region_ids: Optional[list[int]] = None,
        therapist_id: Optional[int] = None,
        patient_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Appointment], int]:
        query = db.query(Appointment).filter(Appointment.deleted_at.is_(None))
        if region_ids:
            query = query.filter(Appointment.region_id.in_(region_ids))
        elif region_id:
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
        return query.offset(skip).limit(limit).all(), total

    @staticmethod
    def update(db: Session, appointment: Appointment, appointment_update: AppointmentUpdate) -> Appointment:
        for field, value in appointment_update.model_dump(exclude_unset=True).items():
            setattr(appointment, field, value)
        db.flush()
        return appointment

    @staticmethod
    def delete(db: Session, appointment_id: int) -> bool:
        return soft_delete(db, Appointment, appointment_id) is not None

    @staticmethod
    def get_therapist(db: Session, therapist_id: int) -> Optional[Therapist]:
        return db.query(Therapist).filter(Therapist.id == therapist_id).first()

    @staticmethod
    def get_slot(db: Session, slot_id: int) -> Optional[SlotMaster]:
        return (
            db.query(SlotMaster)
            .filter(
                SlotMaster.id == slot_id,
                SlotMaster.is_active == 1,
            )
            .first()
        )

    @staticmethod
    def therapist_supports_therapy(db: Session, therapist_id: int, therapy_id: int) -> bool:
        return (
            db.query(TherapistTherapyMapping)
            .filter(
                TherapistTherapyMapping.therapist_id == therapist_id,
                TherapistTherapyMapping.therapy_id == therapy_id,
                TherapistTherapyMapping.is_active == 1,
            )
            .first()
            is not None
        )

    @staticmethod
    def get_plan_item_for_booking(
        db: Session,
        patient_id: int,
        patient_session_plan_id: int,
        therapy_id: int,
    ) -> Optional[PatientSessionPlanItem]:
        return (
            db.query(PatientSessionPlanItem)
            .join(
                PatientSessionPlan,
                PatientSessionPlan.id == PatientSessionPlanItem.patient_session_plan_id,
            )
            .filter(
                PatientSessionPlan.id == patient_session_plan_id,
                PatientSessionPlan.patient_id == patient_id,
                PatientSessionPlan.status_id.in_([401, 403]),
                PatientSessionPlanItem.therapy_id == therapy_id,
            )
            .with_for_update()
            .first()
        )

    @staticmethod
    def get_therapist_leave(
        db: Session,
        therapist_id: int,
        slot_date: date,
    ) -> Optional[TherapistLeave]:
        return (
            db.query(TherapistLeave)
            .filter(
                TherapistLeave.therapist_id == therapist_id,
                TherapistLeave.leave_date == slot_date,
            )
            .first()
        )

    @staticmethod
    def get_active_therapist_slot_mapping(
        db: Session,
        therapist_id: int,
        slot_id: int,
        slot_date: date,
        therapy_id: int,
    ) -> Optional[TherapistSlotMapping]:
        return (
            db.query(TherapistSlotMapping)
            .filter(
                TherapistSlotMapping.therapist_id == therapist_id,
                TherapistSlotMapping.slot_id == slot_id,
                TherapistSlotMapping.slot_date == slot_date,
                TherapistSlotMapping.therapy_id == therapy_id,
                TherapistSlotMapping.status_id != 804,
            )
            .first()
        )

    @staticmethod
    def patient_has_slot_booking(
        db: Session,
        patient_id: int,
        slot_id: int,
        slot_date: date,
        therapy_id: int,
    ) -> bool:
        return (
            db.query(PatientSlotBooking)
            .join(
                TherapistSlotMapping,
                TherapistSlotMapping.id == PatientSlotBooking.therapist_slot_mapping_id,
            )
            .join(
                PatientSessionPlanItem,
                PatientSessionPlanItem.id == PatientSlotBooking.patient_session_plan_item_id,
            )
            .join(
                PatientSessionPlan,
                PatientSessionPlan.id == PatientSessionPlanItem.patient_session_plan_id,
            )
            .filter(
                PatientSessionPlan.patient_id == patient_id,
                TherapistSlotMapping.slot_id == slot_id,
                TherapistSlotMapping.slot_date == slot_date,
                TherapistSlotMapping.therapy_id == therapy_id,
                PatientSlotBooking.status_id != 602,
            )
            .first()
            is not None
        )

    @staticmethod
    def create_therapist_slot_mapping(
        db: Session,
        therapist_id: int,
        slot_id: int,
        slot_date: date,
        therapy_id: int,
    ) -> TherapistSlotMapping:
        mapping = TherapistSlotMapping(
            therapist_id=therapist_id,
            slot_id=slot_id,
            slot_date=slot_date,
            therapy_id=therapy_id,
            status_id=802,
        )
        db.add(mapping)
        db.flush()
        return mapping

    @staticmethod
    def create_patient_slot_booking(
        db: Session,
        therapist_slot_mapping_id: int,
        patient_session_plan_item_id: int,
    ) -> PatientSlotBooking:
        booking = PatientSlotBooking(
            therapist_slot_mapping_id=therapist_slot_mapping_id,
            patient_session_plan_item_id=patient_session_plan_item_id,
            status_id=601,
        )
        db.add(booking)
        db.flush()
        return booking

    @staticmethod
    def cancel_matching_appointment(
        db: Session,
        *,
        patient_id: int,
        therapist_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> Optional[Appointment]:
        appointment = (
            db.query(Appointment)
            .filter(
                Appointment.patient_id == patient_id,
                Appointment.therapist_id == therapist_id,
                Appointment.start_time == start_time,
                Appointment.end_time == end_time,
                Appointment.status != "cancelled",
                Appointment.deleted_at.is_(None),
            )
            .first()
        )

        if appointment:
            appointment.status = "cancelled"
            db.flush()

        return appointment

    @staticmethod
    def get_patient_slot_booking_for_update(
        db: Session,
        patient_slot_booking_id: int,
    ) -> Optional[PatientSlotBooking]:
        return (
            db.query(PatientSlotBooking)
            .filter(PatientSlotBooking.id == patient_slot_booking_id)
            .with_for_update()
            .first()
        )
        
    @staticmethod
    def get_calendar_data(
        db: Session,
        selected_date,
        region_ids
    ):

        query = (
            db.query(
                TherapistSlotMapping.id.label("slot_mapping_id"),
                PatientSlotBooking.id.label("patient_slot_booking_id"),
                Therapist.id.label("therapist_id"),
                Therapist.name.label("therapist_name"),
                SlotMaster.id.label("slot_id"),
                SlotMaster.start_time,
                SlotMaster.end_time,
                SlotMaster.duration_minutes,
                TherapyMaster.id.label("therapy_id"),
                TherapyMaster.name.label("therapy_name"),
                Patient.id.label("patient_id"),
                Patient.first_name.label("patient_first_name"),
                Patient.last_name.label("patient_last_name"),
                Patient.phone.label("patient_phone"),
                PatientSessionPlan.id.label("patient_session_plan_id"),
                PatientSessionPlan.plan_name.label("plan_name"),
                PatientSessionPlan.total_sessions.label("plan_total_sessions"),
                PatientSessionPlanItem.id.label("patient_session_plan_item_id"),
                PatientSessionPlanItem.allocated_sessions,
                PatientSessionPlanItem.assigned_sessions,
                PatientSessionPlanItem.completed_sessions,
                PatientSessionPlanItem.amount_per_session,
                PatientSlotBooking.status_id.label("patient_slot_booking_status_id"),
                TherapistSlotMapping.status_id.label("therapist_slot_mapping_status_id"),
            )
            .join(
                Therapist,
                Therapist.id == TherapistSlotMapping.therapist_id
            )
            .join(
                SlotMaster,
                SlotMaster.id == TherapistSlotMapping.slot_id
            )
            .outerjoin(
                TherapyMaster,
                TherapyMaster.id == TherapistSlotMapping.therapy_id
            )
            .outerjoin(
                PatientSlotBooking,
                and_(
                    PatientSlotBooking.therapist_slot_mapping_id == TherapistSlotMapping.id,
                    PatientSlotBooking.status_id != 602,
                )
            )
            .outerjoin(
                PatientSessionPlanItem,
                PatientSessionPlanItem.id == PatientSlotBooking.patient_session_plan_item_id
            )
            .outerjoin(
                PatientSessionPlan,
                PatientSessionPlan.id == PatientSessionPlanItem.patient_session_plan_id
            )
            .outerjoin(
                Patient,
                Patient.id == PatientSessionPlan.patient_id
            )
            .filter(
                TherapistSlotMapping.slot_date == selected_date,
                Therapist.region_id.in_(region_ids),
                TherapistSlotMapping.status_id != 804,
            )
            .order_by(SlotMaster.start_time)
        )

        return query.all()

    @staticmethod
    def get_active_therapists_for_calendar(
        db: Session,
        region_ids: Optional[list[int]] = None,
    ):
        query = db.query(Therapist).filter(Therapist.is_active == 1)
        if region_ids:
            query = query.filter(Therapist.region_id.in_(region_ids))
        return query.order_by(Therapist.name.asc()).all()


    @staticmethod
    def get_therapist_leaves(
        db: Session,
        selected_date
    ):

        return (
            db.query(TherapistLeave)
            .filter(TherapistLeave.leave_date == selected_date)
            .all()
        )    

    @staticmethod
    def has_active_patient_booking_for_mapping(
        db: Session,
        therapist_slot_mapping_id: int,
        exclude_patient_slot_booking_id: Optional[int] = None,
    ) -> bool:
        query = db.query(PatientSlotBooking).filter(
            PatientSlotBooking.therapist_slot_mapping_id == therapist_slot_mapping_id,
            PatientSlotBooking.status_id != 602,
        )

        if exclude_patient_slot_booking_id:
            query = query.filter(PatientSlotBooking.id != exclude_patient_slot_booking_id)

        return query.first() is not None

    @staticmethod
    def has_conflict(db: Session, therapist_id: int, start_time: datetime, end_time: datetime) -> bool:
        return (
            db.query(Appointment)
            .filter(
                Appointment.therapist_id == therapist_id,
                Appointment.status != "cancelled",
                or_(and_(Appointment.start_time < end_time, Appointment.end_time > start_time)),
                Appointment.deleted_at.is_(None),
            )
            .first()
            is not None
        )
