import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, inspect, or_, text
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies.auth import get_current_user, get_current_admin
from app.models.models import (
    AssessmentMaster,
    AssessmentQuestion,
    AssessmentQuestionOption,
    ChildAssessmentAnswer,
    MASTER_LOOKUP_DATA,
    Patient,
    PatientAssessment,
    PatientAssessmentBilling,
    Payment,
    Role,
    Therapist,
    TherapyAssessmentMapping,
    TherapistTherapyMapping,
    User,
    UserRole,
)

router = APIRouter(prefix="/api/v1/assessments", tags=["assessments"])
ASSESSMENT_ADMIN_ROLES = {"admin", "frontoffice", "front_office", "front_officer"}
ASSESSMENT_CENTRAL_HEAD_ROLES = {"central_head", "centralhead", "centre_head", "centrehead"}


class AnswerPayload(BaseModel):
    question_id: int
    answer_value: str | None = None


class SaveAssessmentAnswersPayload(BaseModel):
    answers: list[AnswerPayload]
    therapist_id: int | None = None


class AssessmentMasterPayload(BaseModel):
    assessment_name: str
    description: str | None = None
    amount: float = 0
    region_id: int | None = None
    display_order: int = 0


class AssessmentQuestionPayload(BaseModel):
    question_text: str
    question_type: str = "textarea"
    display_order: int = 0
    options: list[str] = []


class TherapyAccessPayload(BaseModel):
    therapy_id: int
    assessment_id: int
    can_edit: bool = True


DEFAULT_ASSESSMENTS = [
    ("Language and Communication", "Language and communication assessment", 1),
    ("Occupational Therapy", "Occupational therapy assessment", 2),
    ("Physiotherapy", "Physiotherapy assessment", 3),
    ("Special Education", "Special education assessment", 4),
]


def _ensure_assessment_billing_schema(db: Session) -> None:
    columns = {column["name"] for column in inspect(db.bind).get_columns("assessment_master")}
    if "amount" not in columns:
        db.execute(text("ALTER TABLE assessment_master ADD COLUMN amount FLOAT NOT NULL DEFAULT 0"))
    if "region_id" not in columns:
        db.execute(text("ALTER TABLE assessment_master ADD COLUMN region_id INT NULL"))
    payment_columns = {column["name"] for column in inspect(db.bind).get_columns("payments")}
    if "assessment_source_payment_id" not in payment_columns:
        db.execute(text("ALTER TABLE payments ADD COLUMN assessment_source_payment_id BIGINT NULL"))
    inspector = inspect(db.bind)
    if not inspector.has_table("patient_assessment_billing"):
        db.execute(text("""
            CREATE TABLE patient_assessment_billing (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                patient_id INT NOT NULL,
                assessment_id INT NOT NULL,
                patient_assessment_id INT NULL,
                source_payment_id BIGINT NULL,
                assessment_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
                paid_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
                due_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
                fully_paid BOOLEAN NOT NULL DEFAULT 0,
                payment_status VARCHAR(50) NOT NULL DEFAULT 'UNPAID',
                completed_at DATETIME NULL,
                created_by INT NULL,
                updated_by INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_patient_assessment_billing (patient_id, assessment_id),
                KEY idx_patient_assessment_billing_patient_id (patient_id),
                KEY idx_patient_assessment_billing_assessment_id (assessment_id),
                KEY idx_patient_assessment_billing_patient_assessment_id (patient_assessment_id),
                KEY idx_patient_assessment_billing_source_payment_id (source_payment_id),
                KEY idx_patient_assessment_billing_status (payment_status)
            )
        """))
    else:
        billing_columns = {column["name"] for column in inspector.get_columns("patient_assessment_billing")}
        if "fully_paid" not in billing_columns:
            db.execute(text("ALTER TABLE patient_assessment_billing ADD COLUMN fully_paid BOOLEAN NOT NULL DEFAULT 0"))
            db.execute(text("UPDATE patient_assessment_billing SET fully_paid = CASE WHEN due_amount <= 0 THEN 1 ELSE 0 END"))
    db.commit()


def _assessment_payment_status(amount: float, paid: float, due: float) -> str:
    if amount <= 0 or due <= 0:
        return "PAID"
    if paid > 0:
        return "PARTIAL"
    return "UNPAID"


def _assessment_name(row: AssessmentMaster) -> str:
    return row.assessment_name or row.title


def _question_text(row: AssessmentQuestion) -> str:
    if row.question_text:
        return row.question_text
    if row.question:
        return row.question.question_text
    return f"Question {row.id}"


def _ensure_default_assessments(db: Session) -> None:
    _ensure_assessment_billing_schema(db)
    for name, description, order in DEFAULT_ASSESSMENTS:
        exists = (
            db.query(AssessmentMaster)
            .filter(or_(AssessmentMaster.assessment_name == name, AssessmentMaster.title == name))
            .first()
        )
        if not exists:
            db.add(
                AssessmentMaster(
                    title=name,
                    assessment_name=name,
                    type_id=1,
                    description=description,
                    display_order=order,
                    is_active=True,
                )
            )
    db.commit()


def _can_edit_assessment(db: Session, assessment_id: int, therapist_id: int | None) -> bool:
    if therapist_id is None:
        return False
    return (
        db.query(TherapyAssessmentMapping)
        .filter(
            TherapyAssessmentMapping.assessment_id == assessment_id,
            TherapyAssessmentMapping.can_edit.is_(True),
        )
        .first()
        is not None
    )


def _has_assessment_admin_access(db: Session, user_id: int) -> bool:
    return (
        db.query(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .filter(
            UserRole.user_id == user_id,
            UserRole.deleted_at.is_(None),
            Role.deleted_at.is_(None),
            func.lower(func.replace(Role.name, " ", "_")).in_(list(ASSESSMENT_ADMIN_ROLES)),
        )
        .first()
        is not None
    )


def _normalized_role_names(db: Session, user_id: int) -> set[str]:
    return {
        re.sub(r"[^a-z0-9]+", "_", str(row.name or "").strip().lower()).strip("_")
        for row in (
            db.query(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .filter(
                UserRole.user_id == user_id,
                UserRole.deleted_at.is_(None),
                Role.deleted_at.is_(None),
            )
            .all()
        )
    }


def _current_therapist(db: Session, user: User) -> Therapist | None:
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    therapist = (
        db.query(Therapist)
        .filter(Therapist.user_id == user.id, Therapist.is_active.is_(True))
        .first()
    )
    if not therapist and full_name:
        therapist = (
            db.query(Therapist)
            .filter(Therapist.name == full_name, Therapist.is_active.is_(True))
            .first()
        )
    return therapist


def _mapped_assessment_ids(db: Session, user: User) -> set[int]:
    therapist = _current_therapist(db, user)
    if not therapist:
        return set()

    therapy_ids = [
        row.therapy_id
        for row in (
            db.query(TherapistTherapyMapping.therapy_id)
            .filter(
                TherapistTherapyMapping.therapist_id == therapist.id,
                TherapistTherapyMapping.is_active.is_(True),
            )
            .all()
        )
    ]
    if not therapy_ids:
        return set()

    return {
        row.assessment_id
        for row in (
            db.query(TherapyAssessmentMapping.assessment_id)
            .filter(TherapyAssessmentMapping.therapy_id.in_(therapy_ids))
            .all()
        )
    }


def _visible_assessment_ids(db: Session, user: User) -> set[int] | None:
    roles = _normalized_role_names(db, user.id)
    if not roles or roles & ASSESSMENT_ADMIN_ROLES or roles & ASSESSMENT_CENTRAL_HEAD_ROLES or "therapist" in roles:
        return None
    return set()


def _assessment_is_allowed(assessment: AssessmentMaster, allowed_ids: set[int] | None) -> bool:
    if allowed_ids is None:
        return True
    if not allowed_ids:
        return False
    return assessment.id in allowed_ids


def _can_edit_assessment_for_user(db: Session, user: User, assessment_id: int) -> bool:
    roles = _normalized_role_names(db, user.id)
    if roles & ASSESSMENT_ADMIN_ROLES:
        return True
    if roles & ASSESSMENT_CENTRAL_HEAD_ROLES:
        return True
    return False


def _rbac_allowed(db: Session, user_id: int, code: str, action: str = "view") -> bool:
    column = {
        "view": "can_view",
        "create": "can_create",
        "edit": "can_edit",
        "delete": "can_delete",
    }.get(action, "can_view")
    row = db.execute(text(f"""
        SELECT MAX(permission.{column})
        FROM rbac_resources resource
        JOIN rbac_role_permissions permission ON permission.resource_id = resource.id
        JOIN user_roles user_role ON user_role.role_id = permission.role_id
        WHERE user_role.user_id = :user_id
          AND user_role.deleted_at IS NULL
          AND resource.code = :code
          AND resource.is_active = 1
    """), {"user_id": user_id, "code": code}).first()
    if row and bool(row[0]):
        return True
    if action in {"edit", "create", "delete"}:
        return _has_assessment_admin_access(db, user_id)
    return _has_assessment_admin_access(db, user_id)


@router.get("/children/{child_id}")
def get_child_assessments(
    child_id: int,
    therapist_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = db.query(Patient).filter(Patient.id == child_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Child not found")

    answers = {
        answer.question_id: answer.answer_value
        for answer in db.query(ChildAssessmentAnswer)
        .filter(ChildAssessmentAnswer.child_id == child_id)
        .all()
    }
    can_view_assessments = _rbac_allowed(db, current_user.id, "child.tab.assessment", "view")
    if not can_view_assessments:
        return {"data": []}
    patient_assessments = {
        row.assessment_id: row
        for row in db.query(PatientAssessment)
        .filter(PatientAssessment.patient_id == child_id)
        .all()
    }

    assessments = (
        db.query(AssessmentMaster)
        .options(
            joinedload(AssessmentMaster.type_master),
        )
        .filter(
            AssessmentMaster.is_active.is_(True),
            or_(AssessmentMaster.region_id.is_(None), AssessmentMaster.region_id == patient.region_id),
        )
        .order_by(AssessmentMaster.display_order.asc(), AssessmentMaster.id.asc())
        .all()
    )
    allowed_assessment_ids = _visible_assessment_ids(db, current_user)
    assessments = [
        assessment
        for assessment in assessments
        if _assessment_is_allowed(assessment, allowed_assessment_ids)
    ]

    question_rows = (
        db.query(AssessmentQuestion)
        .options(joinedload(AssessmentQuestion.question), joinedload(AssessmentQuestion.options))
        .order_by(AssessmentQuestion.display_order.asc(), AssessmentQuestion.id.asc())
        .all()
    )
    questions_by_assessment: dict[int, list[AssessmentQuestion]] = {}
    for question in question_rows:
        questions_by_assessment.setdefault(question.assessment_id, []).append(question)

    data = []
    roles = _normalized_role_names(db, current_user.id)
    can_edit_completed = bool(roles & ASSESSMENT_CENTRAL_HEAD_ROLES)
    for assessment in assessments:
        patient_assessment = patient_assessments.get(assessment.id)
        status = patient_assessment.status if patient_assessment else "PENDING"
        data.append(
            {
                "id": assessment.id,
                "assessment_name": _assessment_name(assessment),
                "description": assessment.description,
                "amount": float(getattr(assessment, "amount", 0) or 0),
                "region_id": getattr(assessment, "region_id", None),
                "display_order": assessment.display_order,
                "status": status,
                "completed_date": patient_assessment.completed_date.isoformat() if patient_assessment and patient_assessment.completed_date else None,
                "can_view": True,
                "can_edit": _can_edit_assessment_for_user(db, current_user, assessment.id) and (status != "COMPLETED" or can_edit_completed),
                "questions": [
                    {
                        "id": question.id,
                        "question_text": _question_text(question),
                        "question_type": question.question_type,
                        "display_order": question.display_order,
                        "answer_value": answers.get(question.id),
                        "options": [
                            {
                                "id": option.id,
                                "option_text": option.option_text,
                            }
                            for option in sorted(question.options, key=lambda item: (item.display_order, item.id))
                        ],
                    }
                    for question in questions_by_assessment.get(assessment.id, [])
                ],
            }
        )

    return {"data": data}


@router.put("/children/{child_id}/answers/{assessment_id}")
def save_child_assessment_answers(
    child_id: int,
    assessment_id: int,
    payload: SaveAssessmentAnswersPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not _can_edit_assessment_for_user(db, current_user, assessment_id):
        raise HTTPException(status_code=403, detail="You can edit only assessments allocated to your therapy")

    patient = db.query(Patient).filter(Patient.id == child_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Child not found")

    assessment = (
        db.query(AssessmentMaster)
        .filter(AssessmentMaster.id == assessment_id, AssessmentMaster.is_active.is_(True))
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if getattr(assessment, "region_id", None) is not None and assessment.region_id != patient.region_id:
        raise HTTPException(status_code=403, detail="Assessment is not available for this child's region")

    patient_assessment = (
        db.query(PatientAssessment)
        .filter(
            PatientAssessment.patient_id == child_id,
            PatientAssessment.assessment_id == assessment_id,
        )
        .first()
    )
    roles = _normalized_role_names(db, current_user.id)
    can_edit_completed = bool(roles & ASSESSMENT_CENTRAL_HEAD_ROLES)
    if patient_assessment and patient_assessment.status == "COMPLETED" and not can_edit_completed:
        raise HTTPException(status_code=400, detail="Completed assessment cannot be edited")

    question_ids = [answer.question_id for answer in payload.answers]
    valid_question_ids = {
        row.id
        for row in db.query(AssessmentQuestion.id)
        .filter(
            AssessmentQuestion.assessment_id == assessment_id,
            AssessmentQuestion.id.in_(question_ids),
        )
        .all()
    }

    for answer in payload.answers:
        if answer.question_id not in valid_question_ids:
            continue
        row = (
            db.query(ChildAssessmentAnswer)
            .filter(
                ChildAssessmentAnswer.child_id == child_id,
                ChildAssessmentAnswer.question_id == answer.question_id,
            )
            .first()
        )
        if row:
            row.answer_value = answer.answer_value
            row.updated_by = current_user.id
        else:
            db.add(
                ChildAssessmentAnswer(
                    child_id=child_id,
                    assessment_id=assessment_id,
                    question_id=answer.question_id,
                    answer_value=answer.answer_value,
                    created_by=current_user.id,
                    updated_by=current_user.id,
                )
            )

    in_progress_status_id = MASTER_LOOKUP_DATA["patient_assessment"]["IN_PROGRESS"]
    if patient_assessment:
        if patient_assessment.status != "COMPLETED" or not can_edit_completed:
            patient_assessment.status_id = in_progress_status_id
        patient_assessment.updated_by = current_user.id
    else:
        db.add(
            PatientAssessment(
                patient_id=child_id,
                assessment_id=assessment_id,
                assigned_by=current_user.id,
                status_id=in_progress_status_id,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
        )

    db.commit()
    return {"message": "Assessment answers saved successfully"}


@router.post("/children/{child_id}/complete/{assessment_id}")
def complete_child_assessment(
    child_id: int,
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_assessment_billing_schema(db)
    patient = db.query(Patient).filter(Patient.id == child_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Child not found")

    assessment = (
        db.query(AssessmentMaster)
        .filter(AssessmentMaster.id == assessment_id, AssessmentMaster.is_active.is_(True))
        .first()
    )
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if getattr(assessment, "region_id", None) is not None and assessment.region_id != patient.region_id:
        raise HTTPException(status_code=403, detail="Assessment is not available for this child's region")
    if not _can_edit_assessment_for_user(db, current_user, assessment_id):
        raise HTTPException(status_code=403, detail="You can complete only assessments allocated to your therapy")

    questions = (
        db.query(AssessmentQuestion)
        .filter(AssessmentQuestion.assessment_id == assessment_id)
        .all()
    )
    if not questions:
        raise HTTPException(status_code=400, detail="No questions configured for this assessment")

    answers = {
        answer.question_id: answer.answer_value
        for answer in db.query(ChildAssessmentAnswer)
        .filter(
            ChildAssessmentAnswer.child_id == child_id,
            ChildAssessmentAnswer.assessment_id == assessment_id,
        )
        .all()
    }
    missing_count = sum(1 for question in questions if not str(answers.get(question.id) or "").strip())
    if missing_count:
        raise HTTPException(status_code=400, detail="Please fill all assessment answers before completing")

    completed_status_id = MASTER_LOOKUP_DATA["patient_assessment"]["COMPLETED"]
    patient_assessment = (
        db.query(PatientAssessment)
        .filter(
            PatientAssessment.patient_id == child_id,
            PatientAssessment.assessment_id == assessment_id,
        )
        .first()
    )
    if patient_assessment:
        patient_assessment.status_id = completed_status_id
        patient_assessment.completed_date = datetime.utcnow()
        patient_assessment.updated_by = current_user.id
    else:
        patient_assessment = PatientAssessment(
            patient_id=child_id,
            assessment_id=assessment_id,
            assigned_by=current_user.id,
            status_id=completed_status_id,
            completed_date=datetime.utcnow(),
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(patient_assessment)
        db.flush()

    amount = max(0, float(getattr(assessment, "amount", 0) or 0))
    marker = f"[assessment:{assessment_id}]"
    previous_assessment_due = (
        db.query(func.coalesce(func.sum(PatientAssessmentBilling.due_amount), 0))
        .filter(
            PatientAssessmentBilling.patient_id == child_id,
            PatientAssessmentBilling.assessment_id != assessment_id,
            PatientAssessmentBilling.due_amount > 0,
        )
        .scalar()
        or 0
    )
    charge_due_amount = max(0, float(previous_assessment_due or 0) + amount)
    existing_payment = (
        db.query(Payment)
        .filter(
            Payment.patient_id == child_id,
            Payment.remark.like(f"{marker}%"),
        )
        .order_by(Payment.created_at.asc(), Payment.id.asc())
        .first()
    )
    if amount > 0 and not existing_payment:
        existing_payment = Payment(
            patient_id=child_id,
            amount=amount,
            payment_mode=None,
            payment_amount=0,
            payment_status="UNPAID",
            fully_paid=False,
            due_amount=charge_due_amount,
            payment_date=datetime.utcnow(),
            remark=f"{marker} Assessment completed - {_assessment_name(assessment)}",
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(existing_payment)
        db.flush()
        existing_payment.assessment_source_payment_id = existing_payment.id
    elif existing_payment and not getattr(existing_payment, "assessment_source_payment_id", None):
        existing_payment.assessment_source_payment_id = existing_payment.id
        if not float(getattr(existing_payment, "amount", 0) or 0):
            existing_payment.amount = amount
        if float(getattr(existing_payment, "payment_amount", 0) or 0) <= 0:
            existing_payment.due_amount = charge_due_amount
            existing_payment.fully_paid = charge_due_amount <= 0

    paid_amount = 0.0
    due_amount = charge_due_amount
    if existing_payment:
        chain_id = int(getattr(existing_payment, "assessment_source_payment_id", None) or existing_payment.id)
        latest_payment = (
            db.query(Payment)
            .filter(
                Payment.patient_id == child_id,
                or_(
                    Payment.id == chain_id,
                    Payment.assessment_source_payment_id == chain_id,
                    Payment.remark.like(f"[assessment-payment:{chain_id}]%"),
                ),
            )
            .order_by(Payment.payment_date.desc(), Payment.created_at.desc(), Payment.id.desc())
            .first()
        )
        paid_amount = (
            db.query(func.coalesce(func.sum(Payment.payment_amount), 0))
            .filter(
                Payment.patient_id == child_id,
                or_(
                    Payment.id == chain_id,
                    Payment.assessment_source_payment_id == chain_id,
                    Payment.remark.like(f"[assessment-payment:{chain_id}]%"),
                ),
            )
            .scalar()
            or 0
        )
        due_amount = max(0, amount - float(paid_amount or 0))

    billing = (
        db.query(PatientAssessmentBilling)
        .filter(
            PatientAssessmentBilling.patient_id == child_id,
            PatientAssessmentBilling.assessment_id == assessment_id,
        )
        .first()
    )
    if not billing:
        billing = PatientAssessmentBilling(
            patient_id=child_id,
            assessment_id=assessment_id,
            created_by=current_user.id,
        )
        db.add(billing)
    billing.patient_assessment_id = patient_assessment.id
    billing.source_payment_id = existing_payment.id if existing_payment else None
    billing.assessment_amount = amount
    billing.paid_amount = float(paid_amount or 0)
    billing.due_amount = max(0, float(due_amount or 0))
    billing.fully_paid = float(billing.due_amount or 0) <= 0
    billing.payment_status = _assessment_payment_status(amount, float(paid_amount or 0), float(billing.due_amount or 0))
    billing.completed_at = patient_assessment.completed_date
    billing.updated_by = current_user.id

    db.commit()
    return {
        "message": "Assessment completed successfully",
        "data": {
            "assessment_id": assessment_id,
            "assessment_name": _assessment_name(assessment),
            "amount": amount,
            "payment_id": existing_payment.id if existing_payment else None,
            "billing_id": billing.id,
            "paid_amount": float(billing.paid_amount or 0),
            "due_amount": float(billing.due_amount or 0),
            "payment_status": billing.payment_status,
        },
    }


@router.post("/admin")
def create_assessment(
    payload: AssessmentMasterPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    _ensure_assessment_billing_schema(db)
    row = AssessmentMaster(
        title=payload.assessment_name,
        assessment_name=payload.assessment_name,
        description=payload.description,
        amount=max(0, float(payload.amount or 0)),
        region_id=payload.region_id,
        display_order=payload.display_order,
        is_active=True,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"data": {"id": row.id, "assessment_name": _assessment_name(row)}}


@router.post("/admin/{assessment_id}/questions")
def add_assessment_question(
    assessment_id: int,
    payload: AssessmentQuestionPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    row = AssessmentQuestion(
        assessment_id=assessment_id,
        question_text=payload.question_text,
        question_type=payload.question_type,
        display_order=payload.display_order,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(row)
    db.flush()
    for index, option in enumerate(payload.options):
        db.add(
            AssessmentQuestionOption(
                question_id=row.id,
                option_text=option,
                display_order=index + 1,
            )
        )
    db.commit()
    db.refresh(row)
    return {"data": {"id": row.id}}


@router.put("/admin/therapy-access")
def configure_therapy_access(
    payload: TherapyAccessPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    row = (
        db.query(TherapyAssessmentMapping)
        .filter(
            TherapyAssessmentMapping.therapy_id == payload.therapy_id,
            TherapyAssessmentMapping.assessment_id == payload.assessment_id,
        )
        .first()
    )
    if row:
        row.can_edit = payload.can_edit
    else:
        db.add(TherapyAssessmentMapping(**payload.model_dump()))
    db.commit()
    return {"message": "Therapy assessment access configured"}
