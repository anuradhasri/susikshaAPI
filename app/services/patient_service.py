from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.models.models import Patient, PatientPackage, PatientDuplicate
from app.schemas.schemas import PatientCreate, PatientUpdate
from app.utils.query_utils import soft_delete, filter_by_region, detect_duplicate_patients, check_exact_duplicates


class PatientService:
    """Service for patient operations"""
    
    @staticmethod
    def create_patient(db: Session, patient_create: PatientCreate) -> Patient:
        """Create a new patient"""
        db_patient = Patient(**patient_create.dict())
        
        db.add(db_patient)
        db.commit()
        db.refresh(db_patient)
        
        # Check for duplicates
        duplicates = detect_duplicate_patients(db, db_patient.id, db_patient.region_id)
        for dup_id, similarity, matched_fields in duplicates:
            dup_record = PatientDuplicate(
                patient_id_1=db_patient.id,
                patient_id_2=dup_id,
                similarity_score=similarity,
                matched_fields=matched_fields,
                status="pending"
            )
            db.add(dup_record)
        
        db.commit()
        return db_patient
    
    @staticmethod
    def get_patient_by_id(db: Session, patient_id: int, region_id: int = None) -> Patient:
        """Get patient by ID with region filtering"""
        query = db.query(Patient).filter(
            Patient.id == patient_id,
            Patient.deleted_at.is_(None)
        )
        
        if region_id:
            query = filter_by_region(query, region_id, Patient)
        
        return query.first()
    
    @staticmethod
    def update_patient(db: Session, patient_id: int, patient_update: PatientUpdate, region_id: int = None) -> Patient:
        """Update patient"""
        patient = PatientService.get_patient_by_id(db, patient_id, region_id)
        if not patient:
            return None
        
        for field, value in patient_update.dict(exclude_unset=True).items():
            setattr(patient, field, value)
        
        db.commit()
        db.refresh(patient)
        return patient
    
    @staticmethod
    def delete_patient(db: Session, patient_id: int) -> bool:
        """Soft delete patient"""
        patient = soft_delete(db, Patient, patient_id)
        return patient is not None
    
    @staticmethod
    def list_patients(db: Session, region_id: int = None, skip: int = 0, limit: int = 100) -> tuple:
        """List patients with optional region filtering"""
        query = db.query(Patient).filter(Patient.deleted_at.is_(None))
        
        if region_id:
            query = filter_by_region(query, region_id, Patient)
        
        total = query.count()
        patients = query.offset(skip).limit(limit).all()
        
        return patients, total
    
    @staticmethod
    def get_patient_packages(db: Session, patient_id: int, region_id: int = None) -> list:
        """Get packages for a patient"""
        query = db.query(PatientPackage).filter(
            PatientPackage.patient_id == patient_id,
            PatientPackage.deleted_at.is_(None)
        )
        
        return query.all()
    
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
