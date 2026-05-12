from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.models import Appointment, Therapist
from app.schemas.schemas import AppointmentCreate, AppointmentUpdate
from app.utils.query_utils import filter_by_region, soft_delete


class AppointmentRepository:
    """Database access for calendar appointments."""

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
        therapist_id: Optional[int] = None,
        patient_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Appointment], int]:
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
