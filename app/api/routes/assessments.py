from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.dependencies.auth import get_current_user, get_current_admin
from app.models.models import (
    AssessmentMaster,
    AssessmentQuestion,
    AssessmentQuestionOption,
    ChildAssessmentAnswer,
    Patient,
    Role,
    TherapyAssessmentMapping,
    User,
    UserRole,
)

router = APIRouter(prefix="/api/v1/assessments", tags=["assessments"])


class AnswerPayload(BaseModel):
    question_id: int
    answer_value: str | None = None


class SaveAssessmentAnswersPayload(BaseModel):
    answers: list[AnswerPayload]
    therapist_id: int | None = None


class AssessmentMasterPayload(BaseModel):
    assessment_name: str
    description: str | None = None
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
    ("Behavioural Assessment", "Behaviour, play, attention, and social skill observations", 1),
    ("Occupational Therapy Assessment", "Motor, ADL, visual motor, and OT observations", 2),
    ("Speech Assessment", "Speech, language, communication, and findings", 3),
    ("Sensory Profile", "Sensory processing profile across domains", 4),
    ("Goals & Recommendations", "Therapy goals, recommendations, and next steps", 5),
]


def _assessment_name(row: AssessmentMaster) -> str:
    return row.assessment_name or row.title


def _question_text(row: AssessmentQuestion) -> str:
    if row.question_text:
        return row.question_text
    if row.question:
        return row.question.question_text
    return f"Question {row.id}"


def _ensure_default_assessments(db: Session) -> None:
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


def _is_admin(db: Session, user_id: int) -> bool:
    return (
        db.query(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .filter(
            UserRole.user_id == user_id,
            UserRole.deleted_at.is_(None),
            Role.deleted_at.is_(None),
            func.lower(Role.name) == "admin",
        )
        .first()
        is not None
    )


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

    _ensure_default_assessments(db)

    answers = {
        answer.question_id: answer.answer_value
        for answer in db.query(ChildAssessmentAnswer)
        .filter(ChildAssessmentAnswer.child_id == child_id)
        .all()
    }
    edit_mapping_rows = (
        db.query(TherapyAssessmentMapping)
        .filter(TherapyAssessmentMapping.can_edit.is_(True))
        .all()
    )
    editable_assessment_ids = (
        {assessment.id for assessment in db.query(AssessmentMaster.id).all()}
        if _is_admin(db, current_user.id)
        else {row.assessment_id for row in edit_mapping_rows} if therapist_id else set()
    )

    assessments = (
        db.query(AssessmentMaster)
        .options(
            joinedload(AssessmentMaster.type_master),
        )
        .filter(AssessmentMaster.is_active.is_(True))
        .order_by(AssessmentMaster.display_order.asc(), AssessmentMaster.id.asc())
        .all()
    )

    question_rows = (
        db.query(AssessmentQuestion)
        .options(joinedload(AssessmentQuestion.question), joinedload(AssessmentQuestion.options))
        .order_by(AssessmentQuestion.display_order.asc(), AssessmentQuestion.id.asc())
        .all()
    )
    questions_by_assessment: dict[int, list[AssessmentQuestion]] = {}
    for question in question_rows:
        questions_by_assessment.setdefault(question.assessment_id, []).append(question)

    return {
        "data": [
            {
                "id": assessment.id,
                "assessment_name": _assessment_name(assessment),
                "description": assessment.description,
                "display_order": assessment.display_order,
                "can_edit": assessment.id in editable_assessment_ids,
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
            for assessment in assessments
        ]
    }


@router.put("/children/{child_id}/answers/{assessment_id}")
def save_child_assessment_answers(
    child_id: int,
    assessment_id: int,
    payload: SaveAssessmentAnswersPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not _is_admin(db, current_user.id) and not _can_edit_assessment(db, assessment_id, payload.therapist_id):
        raise HTTPException(status_code=403, detail="Assessment is read only for this therapy")

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

    db.commit()
    return {"message": "Assessment answers saved successfully"}


@router.post("/admin")
def create_assessment(
    payload: AssessmentMasterPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    row = AssessmentMaster(
        title=payload.assessment_name,
        assessment_name=payload.assessment_name,
        description=payload.description,
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
