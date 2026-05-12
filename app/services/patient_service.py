from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.models.models import Patient, PatientPackage, PatientDuplicate
from app.repositories.patient_repository import PatientRepository
from app.schemas.schemas import PatientCreate, PatientUpdate
from app.utils.query_utils import soft_delete, filter_by_region, detect_duplicate_patients, check_exact_duplicates


class PatientService:
    """Service for patient operations"""
    
    @staticmethod
    def create_patient(db: Session, patient_create: PatientCreate) -> Patient:
        """Create a new patient"""
        db_patient = PatientRepository.create(db, patient_create)
        
        # Check for duplicates
        duplicates = detect_duplicate_patients(db, db_patient.id, db_patient.region_id)
        for dup_id, similarity, matched_fields in duplicates:
            PatientRepository.add_duplicate(db, db_patient.id, dup_id, similarity, matched_fields)
        
        db.commit()
        return db_patient
    
    @staticmethod
    def get_patient_by_id(db: Session, patient_id: int, region_id: int = None) -> Patient:
        """Get patient by ID with region filtering"""
        return PatientRepository.get_by_id(db, patient_id, region_id)
    
    @staticmethod
    def update_patient(db: Session, patient_id: int, patient_update: PatientUpdate, region_id: int = None) -> Patient:
        """Update patient"""
        patient = PatientService.get_patient_by_id(db, patient_id, region_id)
        if not patient:
            return None
        
        PatientRepository.update(db, patient, patient_update)
        db.commit()
        db.refresh(patient)
        return patient
    
    @staticmethod
    def delete_patient(db: Session, patient_id: int) -> bool:
        """Soft delete patient"""
        return PatientRepository.delete(db, patient_id)
    
    @staticmethod
    def list_patients(db: Session, region_id: int = None, skip: int = 0, limit: int = 100) -> tuple:
        """List patients with optional region filtering"""
        return PatientRepository.list(db, region_id, skip, limit)
    
    @staticmethod
    def get_patient_packages(db: Session, patient_id: int, region_id: int = None) -> list:
        """Get packages for a patient"""
        return PatientRepository.list_packages(db, patient_id)
    
    @staticmethod
    def check_package_availability(db: Session, patient_id: int) -> dict:
        """Check if patient has available sessions in any package"""
        packages = PatientService.get_patient_packages(db, patient_id)
        
        available_packages = []
        for pkg in packages:
            if pkg.sessions_remaining > 0:
                available_packages.append({
                    "id": pkg.id,
                    "package_name": pkg.package.name,
                    "sessions_remaining": pkg.sessions_remaining,
                    "total_sessions": pkg.package.total_sessions
                })
        
        return {
            "has_available_sessions": len(available_packages) > 0,
            "available_packages": available_packages
        }
    
    @staticmethod
    def update_package_sessions(db: Session, patient_package_id: int, completed: bool = True) -> PatientPackage:
        """Update package session count"""
        pkg = db.query(PatientPackage).filter(
            PatientPackage.id == patient_package_id
        ).first()
        
        if pkg and completed:
            pkg.sessions_completed += 1
            pkg.sessions_remaining -= 1
            
            if pkg.sessions_remaining == 0:
                pkg.status = "completed"
        
        db.commit()
        db.refresh(pkg)
        return pkg
    
    @staticmethod
    def get_duplicate_patients(db: Session, patient_id: int) -> list:
        """Get potential duplicate patients"""
        duplicates = db.query(PatientDuplicate).filter(
            or_(
                PatientDuplicate.patient_id_1 == patient_id,
                PatientDuplicate.patient_id_2 == patient_id
            ),
            PatientDuplicate.deleted_at.is_(None)
        ).all()
        
        return duplicates
