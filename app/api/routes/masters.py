from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.models import PaymentModeMaster, StatusMaster, User
from app.schemas.schemas import MasterOptionResponse

router = APIRouter(prefix="/api/v1/masters", tags=["masters"])


@router.get("/statuses", response_model=List[MasterOptionResponse])
async def list_statuses(
    category: str = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(StatusMaster).filter(StatusMaster.is_active == 1)
    if category:
        query = query.filter(StatusMaster.category == category)

    rows = query.order_by(StatusMaster.category.asc(), StatusMaster.id.asc()).all()
    return [
        {
            "id": row.id,
            "code": row.code,
            "name": row.name,
            "category": row.category,
        }
        for row in rows
    ]


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
