from sqlalchemy.orm import Query, Session
from sqlalchemy import and_, or_
from typing import Any
from difflib import SequenceMatcher
from app.models.models import Patient


def filter_by_region(query: Query, user_region_id: int, model: Any) -> Query:
    """
    Apply region-based filtering to query
    Non-admin users can only access their region's data
    """
    if hasattr(model, "region_id"):
        query = query.filter(model.region_id == user_region_id)
    
    return query


def calculate_similarity(str1: str, str2: str) -> float:
    """Calculate string similarity score (0-1)"""
    if not str1 or not str2:
        return 0.0
    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()


def detect_duplicate_patients(db: Session, patient_id: int, region_id: int, threshold: float = 0.85) -> list:
    """
    Detect potential duplicate patients based on similarity
    Compares name, email, and phone
    Returns list of (patient_id, similarity_score, matched_fields)
    """
    patient = db.query(Patient).filter(
        Patient.id == patient_id,
        Patient.deleted_at.is_(None)
    ).first()
    
    if not patient:
        return []
    
    duplicates = []
    
    # Get all other patients in the same region
    other_patients = db.query(Patient).filter(
        Patient.region_id == region_id,
        Patient.id != patient_id,
        Patient.deleted_at.is_(None)
    ).all()
    
    for other in other_patients:
        matched_fields = {}
        similarity_scores = []
        
        # Compare names
        first_name_sim = calculate_similarity(patient.first_name, other.first_name)
        last_name_sim = calculate_similarity(patient.last_name, other.last_name)
        
        if first_name_sim > threshold:
            matched_fields["first_name"] = first_name_sim
            similarity_scores.append(first_name_sim)
        
        if last_name_sim > threshold:
            matched_fields["last_name"] = last_name_sim
            similarity_scores.append(last_name_sim)
        
        # Compare email
        if patient.email and other.email:
            email_sim = calculate_similarity(patient.email, other.email)
            if email_sim > threshold:
                matched_fields["email"] = email_sim
                similarity_scores.append(email_sim)
        
        # Compare phone
        if patient.phone and other.phone:
            phone_sim = calculate_similarity(patient.phone, other.phone)
            if phone_sim > threshold:
                matched_fields["phone"] = phone_sim
                similarity_scores.append(phone_sim)
        
        # Calculate overall similarity
        if similarity_scores:
            overall_similarity = sum(similarity_scores) / len(similarity_scores)
            if overall_similarity >= threshold:
                duplicates.append((other.id, overall_similarity, matched_fields))
    
    return duplicates


def format_phone(phone: str) -> str:
    """Format phone number for comparison"""
    # Remove all non-digit characters
    return "".join(filter(str.isdigit, phone))


def check_exact_duplicates(db: Session, patient: Patient) -> list:
    """Check for exact duplicates (same email or phone)"""
    duplicates = []
    
    if patient.email:
        existing = db.query(Patient).filter(
            Patient.email == patient.email,
            Patient.id != patient.id,
            Patient.deleted_at.is_(None)
        ).all()
        duplicates.extend(existing)
    
    if patient.phone:
        formatted_phone = format_phone(patient.phone)
        existing = db.query(Patient).filter(
            Patient.id != patient.id,
            Patient.deleted_at.is_(None)
        ).all()
        
        for p in existing:
            if p.phone and format_phone(p.phone) == formatted_phone:
                if p not in duplicates:
                    duplicates.append(p)
    
    return duplicates


def soft_delete(db: Session, model: Any, id: int):
    """Soft delete a record"""
    from datetime import datetime
    
    record = db.query(model).filter(model.id == id).first()
    if record:
        record.deleted_at = datetime.utcnow()
        db.commit()
    return record
