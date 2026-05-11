"""
Database Initialization Script

This script initializes the database with default roles and sample data.
Run this once after creating the database schema.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import SessionLocal, init_db
from app.models.models import (
    Alert,
    Appointment,
    Invoice,
    InvoiceItem,
    Package,
    Patient,
    PatientPackage,
    Payment,
    Region,
    Role,
    Session as TherapySession,
    SessionAssignment,
    Therapist,
    TherapistAvailability,
    User,
    UserRole,
    WaitlistEntry,
)
from app.core.security import hash_password
from datetime import date, datetime, time, timedelta


def init_database():
    """Initialize database with default data"""
    db = SessionLocal()
    
    try:
        # Create tables
        init_db()
        db.execute(text("ALTER TABLE sessions MODIFY status ENUM('SCHEDULED','IN_PROGRESS','COMPLETED','CANCELLED','NO_SHOW')"))
        db.execute(text("ALTER TABLE appointments MODIFY status ENUM('SCHEDULED','CONFIRMED','IN_PROGRESS','COMPLETED','CANCELLED','NO_SHOW')"))
        db.commit()
        print("[OK] Database tables created")
        
        # Create default roles
        roles_data = [
            {"name": "admin", "description": "System administrator with full access"},
            {"name": "therapist", "description": "Therapist with access to assigned patients and sessions"},
            {"name": "front_office", "description": "Front office staff with access to appointments and billing"}
        ]
        
        existing_roles = db.query(Role).filter(Role.deleted_at.is_(None)).all()
        if not existing_roles:
            for role_data in roles_data:
                role = Role(**role_data)
                db.add(role)
            db.commit()
            print("[OK] Default roles created")
        else:
            print("[OK] Roles already exist")
        
        # Create default regions
        regions_data = [
            {"name": "North Region", "code": "NR", "location": "Northern District"},
            {"name": "South Region", "code": "SR", "location": "Southern District"},
            {"name": "East Region", "code": "ER", "location": "Eastern District"},
            {"name": "West Region", "code": "WR", "location": "Western District"}
        ]
        
        existing_regions = db.query(Region).filter(Region.deleted_at.is_(None)).all()
        if not existing_regions:
            for region_data in regions_data:
                region = Region(**region_data)
                db.add(region)
            db.commit()
            print("[OK] Default regions created")
        else:
            print("[OK] Regions already exist")
        
        # Create admin user
        existing_admin = db.query(User).filter(
            User.username == "admin",
            User.deleted_at.is_(None)
        ).first()
        
        if not existing_admin:
            admin_region = db.query(Region).filter(Region.code == "NR").first()
            admin_user = User(
                username="admin",
                email="admin@autism-therapy.com",
                hashed_password=hash_password("admin123"),
                first_name="System",
                last_name="Administrator",
                region_id=admin_region.id,
                is_active=True,
                is_verified=True
            )
            db.add(admin_user)
            db.flush()
            
            # Assign admin role
            admin_role = db.query(Role).filter(Role.name == "admin").first()
            user_role = UserRole(user_id=admin_user.id, role_id=admin_role.id)
            db.add(user_role)
            
            db.commit()
            print("[OK] Admin user created (username: admin, password: admin123)")
        else:
            print("[OK] Admin user already exists")
        
        # Create sample therapist user
        existing_therapist = db.query(User).filter(
            User.username == "therapist1",
            User.deleted_at.is_(None)
        ).first()
        
        if not existing_therapist:
            north_region = db.query(Region).filter(Region.code == "NR").first()
            therapist_user = User(
                username="therapist1",
                email="therapist1@autism-therapy.com",
                hashed_password=hash_password("therapist123"),
                first_name="Sarah",
                last_name="Johnson",
                region_id=north_region.id,
                phone="555-0001",
                is_active=True,
                is_verified=True
            )
            db.add(therapist_user)
            db.flush()
            
            # Assign therapist role
            therapist_role = db.query(Role).filter(Role.name == "therapist").first()
            user_role = UserRole(user_id=therapist_user.id, role_id=therapist_role.id)
            db.add(user_role)
            
            # Create therapist profile
            therapist_profile = Therapist(
                user_id=therapist_user.id,
                license_number="LIC-001",
                specialization="Autism Spectrum Disorder",
                qualification="M.Sc. in Special Education",
                is_available=True,
                region_id=north_region.id
            )
            db.add(therapist_profile)
            
            db.commit()
            print("[OK] Sample therapist created (username: therapist1, password: therapist123)")
        else:
            print("[OK] Sample therapist already exists")
        
        # Create sample front office user
        existing_fo = db.query(User).filter(
            User.username == "frontoffice1",
            User.deleted_at.is_(None)
        ).first()
        
        if not existing_fo:
            north_region = db.query(Region).filter(Region.code == "NR").first()
            fo_user = User(
                username="frontoffice1",
                email="frontoffice1@autism-therapy.com",
                hashed_password=hash_password("frontoffice123"),
                first_name="Michael",
                last_name="Brown",
                region_id=north_region.id,
                phone="555-0002",
                is_active=True,
                is_verified=True
            )
            db.add(fo_user)
            db.flush()
            
            # Assign front office role
            fo_role = db.query(Role).filter(Role.name == "front_office").first()
            user_role = UserRole(user_id=fo_user.id, role_id=fo_role.id)
            db.add(user_role)
            
            db.commit()
            print("[OK] Sample front office user created (username: frontoffice1, password: frontoffice123)")
        else:
            print("[OK] Sample front office user already exists")
        
        # Create sample patients
        north_region = db.query(Region).filter(Region.code == "NR").first()
        existing_patients = db.query(Patient).filter(
            Patient.region_id == north_region.id,
            Patient.deleted_at.is_(None)
        ).count()
        
        if existing_patients == 0:
            patients_data = [
                {
                    "first_name": "Emma",
                    "last_name": "Wilson",
                    "date_of_birth": date(2015, 3, 15),
                    "gender": "Female",
                    "email": "emma.wilson@email.com",
                    "phone": "555-1001",
                    "diagnosis": "ASD Level 2",
                    "region_id": north_region.id
                },
                {
                    "first_name": "Noah",
                    "last_name": "Davis",
                    "date_of_birth": date(2014, 7, 22),
                    "gender": "Male",
                    "email": "noah.davis@email.com",
                    "phone": "555-1002",
                    "diagnosis": "ASD Level 1",
                    "region_id": north_region.id
                },
                {
                    "first_name": "Olivia",
                    "last_name": "Martinez",
                    "date_of_birth": date(2016, 1, 10),
                    "gender": "Female",
                    "email": "olivia.martinez@email.com",
                    "phone": "555-1003",
                    "diagnosis": "ASD Level 3",
                    "region_id": north_region.id
                }
            ]
            
            for patient_data in patients_data:
                patient = Patient(**patient_data)
                db.add(patient)
            
            db.commit()
            print("[OK] Sample patients created")
        else:
            print("[OK] Sample patients already exist")

        # Create a second therapist so utilization and reassignment reports have useful contrast
        existing_therapist_2 = db.query(User).filter(
            User.username == "therapist2",
            User.deleted_at.is_(None)
        ).first()
        if not existing_therapist_2:
            therapist_user_2 = User(
                username="therapist2",
                email="therapist2@autism-therapy.com",
                hashed_password=hash_password("therapist123"),
                first_name="Anita",
                last_name="Rao",
                region_id=north_region.id,
                phone="555-0003",
                is_active=True,
                is_verified=True
            )
            db.add(therapist_user_2)
            db.flush()
            therapist_role = db.query(Role).filter(Role.name == "therapist").first()
            db.add(UserRole(user_id=therapist_user_2.id, role_id=therapist_role.id))
            db.add(Therapist(
                user_id=therapist_user_2.id,
                license_number="LIC-002",
                specialization="Speech and Behaviour Therapy",
                qualification="MOT, Behaviour Intervention Certified",
                is_available=True,
                region_id=north_region.id
            ))
            db.commit()
            print("[OK] Second sample therapist created (username: therapist2, password: therapist123)")

        therapists = db.query(Therapist).filter(
            Therapist.region_id == north_region.id,
            Therapist.deleted_at.is_(None)
        ).order_by(Therapist.id).all()
        patients = db.query(Patient).filter(
            Patient.region_id == north_region.id,
            Patient.deleted_at.is_(None)
        ).order_by(Patient.id).all()

        if db.query(Package).filter(Package.deleted_at.is_(None)).count() == 0:
            db.add_all([
                Package(name="Early Intervention 12", description="12 session starter plan", total_sessions=12, price=18000, duration_days=60),
                Package(name="Behaviour Therapy 20", description="20 session intensive plan", total_sessions=20, price=36000, duration_days=90),
                Package(name="Speech Therapy 8", description="8 session speech block", total_sessions=8, price=12000, duration_days=45),
            ])
            db.commit()
            print("[OK] Sample packages created")

        packages = db.query(Package).filter(Package.deleted_at.is_(None)).order_by(Package.id).all()

        if patients and packages and db.query(PatientPackage).filter(PatientPackage.deleted_at.is_(None)).count() == 0:
            for idx, patient in enumerate(patients):
                package = packages[idx % len(packages)]
                completed = min([4, 8, 2][idx % 3], package.total_sessions)
                db.add(PatientPackage(
                    patient_id=patient.id,
                    package_id=package.id,
                    start_date=date.today() - timedelta(days=35 - (idx * 5)),
                    end_date=date.today() + timedelta(days=45 + (idx * 10)),
                    sessions_completed=completed,
                    sessions_remaining=max(0, package.total_sessions - completed),
                    status="active" if completed < package.total_sessions else "completed"
                ))
            db.commit()
            print("[OK] Sample patient packages created")

        if patients and therapists and db.query(Appointment).filter(Appointment.deleted_at.is_(None)).count() == 0:
            appointment_specs = [
                (-12, 9, "completed", 0, 0),
                (-7, 10, "completed", 1, 0),
                (-2, 11, "completed", 2, 1 if len(therapists) > 1 else 0),
                (0, 14, "confirmed", 0, 0),
                (1, 10, "scheduled", 1, 1 if len(therapists) > 1 else 0),
                (5, 16, "scheduled", 2, 0),
            ]
            for offset, hour, status_value, patient_idx, therapist_idx in appointment_specs:
                patient = patients[patient_idx % len(patients)]
                therapist = therapists[therapist_idx % len(therapists)]
                start_time = datetime.combine(date.today() + timedelta(days=offset), time(hour, 0))
                db.add(Appointment(
                    patient_id=patient.id,
                    therapist_id=therapist.id,
                    start_time=start_time,
                    end_time=start_time + timedelta(minutes=60),
                    status=status_value,
                    notes=f"{status_value.title()} appointment for {patient.first_name}",
                    region_id=north_region.id
                ))
            db.commit()
            print("[OK] Sample appointments created")

        if patients and therapists and db.query(TherapySession).filter(TherapySession.deleted_at.is_(None)).count() == 0:
            session_specs = [
                (-30, "completed", "paid", 1, "Strong engagement and improved eye contact."),
                (-22, "completed", "billed", 2, "Followed two-step instructions with prompting."),
                (-15, "completed", "pending", 3, "Completed sensory regulation activities."),
                (-5, "completed", "pending", 4, "Parent handover completed."),
                (0, "in_progress", "pending", 5, "Currently working on transition tolerance."),
                (3, "scheduled", "pending", 6, "Upcoming gross motor session."),
                (10, "scheduled", "pending", 7, "Upcoming language building session."),
            ]
            for idx, (offset, status_value, billing_status, number, notes) in enumerate(session_specs):
                patient = patients[idx % len(patients)]
                therapist = therapists[idx % len(therapists)]
                session = TherapySession(
                    patient_id=patient.id,
                    therapist_id=therapist.id,
                    session_number=number,
                    duration_minutes=60,
                    status=status_value,
                    session_date=date.today() + timedelta(days=offset),
                    progress_notes=notes,
                    billing_status=billing_status
                )
                db.add(session)
                db.flush()
                if packages:
                    db.add(SessionAssignment(session_id=session.id, package_id=packages[idx % len(packages)].id))
            db.commit()
            print("[OK] Sample sessions created")

        if patients and db.query(Invoice).filter(Invoice.deleted_at.is_(None)).count() == 0:
            invoice_specs = [
                ("INV-2026-001", 0, -25, 18000, 18000, "paid", "Early Intervention 12"),
                ("INV-2026-002", 1, -12, 36000, 12000, "issued", "Behaviour Therapy 20"),
                ("INV-2026-003", 2, -45, 12000, 0, "issued", "Speech Therapy 8"),
                ("INV-2026-004", 0, 10, 6000, 0, "draft", "Additional therapy sessions"),
            ]
            for invoice_number, patient_idx, due_offset, total, paid, status_value, description in invoice_specs:
                patient = patients[patient_idx % len(patients)]
                invoice = Invoice(
                    invoice_number=invoice_number,
                    patient_id=patient.id,
                    region_id=north_region.id,
                    issue_date=date.today() + timedelta(days=due_offset - 14),
                    due_date=date.today() + timedelta(days=due_offset),
                    total_amount=total,
                    paid_amount=paid,
                    status=status_value,
                    description=description
                )
                db.add(invoice)
                db.flush()
                db.add(InvoiceItem(invoice_id=invoice.id, description=description, quantity=1, unit_price=total, total_price=total))
                if paid > 0:
                    db.add(Payment(
                        invoice_id=invoice.id,
                        amount=paid,
                        payment_method="online" if paid == total else "bank_transfer",
                        transaction_id=f"TXN-{invoice_number[-3:]}",
                        status="completed",
                        payment_date=datetime.utcnow() - timedelta(days=max(1, abs(due_offset) // 2)),
                        notes="Seed payment for analytics"
                    ))
            db.commit()
            print("[OK] Sample billing data created")

        if patients and db.query(Alert).filter(Alert.deleted_at.is_(None)).count() == 0:
            db.add_all([
                Alert(patient_id=patients[0].id, alert_type="session_leakage", title="Unbilled Completed Session", description="A completed session has not been billed yet.", severity="medium", is_active=True, metadata_json={"entity_type": "sessions", "entity_id": 3}),
                Alert(patient_id=patients[1].id if len(patients) > 1 else patients[0].id, alert_type="overdue_payment", title="Overdue Invoice", description="Invoice INV-2026-003 is past due and unpaid.", severity="critical", is_active=True, metadata_json={"entity_type": "invoices", "entity_id": 3}),
                Alert(patient_id=patients[2].id if len(patients) > 2 else patients[0].id, alert_type="package_expiry", title="Package Expiring Soon", description="Patient package has fewer than 3 sessions remaining.", severity="low", is_active=True, metadata_json={"entity_type": "patient_packages", "entity_id": 3}),
            ])
            db.commit()
            print("[OK] Sample alerts created")

        if patients and therapists and db.query(WaitlistEntry).filter(WaitlistEntry.deleted_at.is_(None)).count() == 0:
            db.add_all([
                WaitlistEntry(patient_id=patients[0].id, requested_service="Speech Therapy", preferred_therapist_id=therapists[1].id if len(therapists) > 1 else therapists[0].id, priority="high", preferred_days=["Mon", "Wed"], preferred_time="After 4 PM", notes="Parent requested evening sessions.", status="waiting"),
                WaitlistEntry(patient_id=patients[1].id if len(patients) > 1 else patients[0].id, requested_service="Behaviour Therapy", preferred_therapist_id=therapists[0].id, priority="medium", preferred_days=["Tue", "Thu"], preferred_time="Morning", notes="Can start from next week.", status="waiting"),
            ])
            db.commit()
            print("[OK] Sample waitlist entries created")

        if therapists and db.query(TherapistAvailability).filter(TherapistAvailability.deleted_at.is_(None)).count() == 0:
            month_start = date.today().replace(day=1)
            next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            current = month_start
            while current < next_month:
                if current.weekday() < 6:
                    for therapist in therapists:
                        db.add(TherapistAvailability(
                            therapist_id=therapist.id,
                            availability_date=current,
                            start_time=time(9, 0),
                            end_time=time(17, 0),
                            break_start=time(13, 0),
                            break_end=time(14, 0),
                            status="available",
                            notes="Default working day"
                        ))
                current += timedelta(days=1)
            db.commit()
            print("[OK] Sample therapist availability created")
        
        print("\n[OK] Database initialization completed successfully!")
        print("\nSample login credentials:")
        print("  Admin:        admin / admin123")
        print("  Therapist:    therapist1 / therapist123")
        print("  Front Office: frontoffice1 / frontoffice123")
        print("\nAPI Documentation: http://localhost:8000/api/docs")
        
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Error during initialization: {str(e)}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_database()
