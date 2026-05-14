from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import Patient, PatientDuplicate, PatientPackage
from app.schemas.schemas import PatientCreate, PatientUpdate
from app.utils.query_utils import filter_by_region, soft_delete


class PatientRepository:
    """Database access for patients and patient-owned records."""

    @staticmethod
    def _create_payload(patient_create: PatientCreate) -> dict:
        data = patient_create.model_dump()
        data["alternate_contact"] = data.pop("emergency_contact", None)
        data.pop("medical_history", None)
        return data

    @staticmethod
    def create(db: Session, patient_create: PatientCreate) -> Patient:
        patient = Patient(**PatientRepository._create_payload(patient_create))
        db.add(patient)
        db.flush()
        return patient

    @staticmethod
    def get_by_id(db: Session, patient_id: int, region_id: Optional[int] = None) -> Optional[Patient]:
        query = db.query(Patient).filter(Patient.id == patient_id)
        if hasattr(Patient, "deleted_at"):
            query = query.filter(Patient.deleted_at.is_(None))
        if region_id:
            query = filter_by_region(query, region_id, Patient)
        return query.first()

    # @staticmethod
    # def list(db: Session, region_id: Optional[int] = None, skip: int = 0, limit: int = 100) -> tuple[list[Patient], int]:
    #     query = db.query(Patient)
    #     if hasattr(Patient, "deleted_at"):
    #         query = query.filter(Patient.deleted_at.is_(None))
    #     if region_id:
    #         query = filter_by_region(query, region_id, Patient)
    #     total = query.count()
    #     return query.offset(skip).limit(limit).all(), total
    
    @staticmethod
    def list(db: Session, region_ids: list[int] = None, skip: int = 0, limit: int = 100) -> tuple[list[Patient], int]:
        query = db.query(Patient)
        if hasattr(Patient, "deleted_at"):
            query = query.filter(Patient.deleted_at.is_(None))
        if region_ids:
            query = query.filter(Patient.region_id.in_(region_ids))
        total = query.count()
        return query.offset(skip).limit(limit).all(), total

    @staticmethod
    def update(db: Session, patient: Patient, patient_update: PatientUpdate) -> Patient:
        data = patient_update.model_dump(exclude_unset=True)
        field_map = {
            "emergency_contact": "alternate_contact",
        }
        data.pop("medical_history", None)
        for field, value in data.items():
            setattr(patient, field_map.get(field, field), value)
        db.flush()
        return patient

    @staticmethod
    def delete(db: Session, patient_id: int) -> bool:
        return soft_delete(db, Patient, patient_id) is not None

    @staticmethod
    def add_duplicate(db: Session, patient_id: int, duplicate_id: int, similarity: float, matched_fields: dict) -> PatientDuplicate:
        duplicate = PatientDuplicate(
            patient_id_1=patient_id,
            patient_id_2=duplicate_id,
            similarity_score=similarity,
            matched_fields=matched_fields,
            status="pending",
        )
        db.add(duplicate)
        db.flush()
        return duplicate

    @staticmethod
    def list_packages(db: Session, patient_id: int) -> list[PatientPackage]:
        return (
            db.query(PatientPackage)
            .filter(PatientPackage.patient_id == patient_id, PatientPackage.deleted_at.is_(None))
            .all()
        )
