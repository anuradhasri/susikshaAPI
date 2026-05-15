from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import MASTER_LOOKUP_MODELS, PaymentModeMaster, User
from app.schemas.schemas import MasterOptionResponse

router = APIRouter(prefix="/api/v1/masters", tags=["masters"])

STATUS_LOOKUP_CATEGORIES = {
    "invoice",
    "patient_session_plan",
    "patient_assessment",
    "patient_slot_booking",
    "patient_therapy",
    "therapist_slot_mapping",
}


@router.get("/statuses", response_model=List[MasterOptionResponse])
async def list_statuses(
    category: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    categories = [category] if category else sorted(STATUS_LOOKUP_CATEGORIES)
    rows = []

    for current_category in categories:
        model = MASTER_LOOKUP_MODELS.get(current_category)
        if model is None:
            continue

        lookup_rows = (
            db.query(model)
            .filter(model.is_active == 1)
            .order_by(model.id.asc())
            .all()
        )
        rows.extend(
            {
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "category": current_category,
            }
            for row in lookup_rows
        )

    return rows


@router.get("/payment-modes", response_model=List[MasterOptionResponse])
async def list_payment_modes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(PaymentModeMaster)
        .filter(PaymentModeMaster.is_active == 1)
        .order_by(PaymentModeMaster.payment_mode_name.asc())
        .all()
    )
    return [
        {
            "id": row.id,
            "code": None,
            "name": row.payment_mode_name,
            "category": "payment_mode",
        }
        for row in rows
    ]
