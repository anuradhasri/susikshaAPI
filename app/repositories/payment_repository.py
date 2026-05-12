from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import Payment
from app.schemas.schemas import PaymentCreate


class PaymentRepository:
    """Database access for patient payments."""

    @staticmethod
    def create(db: Session, payment_create: PaymentCreate, *, created_by: Optional[int] = None) -> Payment:
        payment = Payment(
            patient_id=payment_create.patient_id,
            payment_amount=payment_create.payment_amount,
            payment_mode=payment_create.payment_mode,
            payment_status=payment_create.payment_status,
            remark=payment_create.remark,
            payment_date=payment_create.payment_date,
            created_by=created_by,
            updated_by=created_by,
        )
        db.add(payment)
        db.flush()
        return payment

    @staticmethod
    def get_by_id(db: Session, payment_id: int) -> Optional[Payment]:
        return db.query(Payment).filter(Payment.id == payment_id).first()

    @staticmethod
    def list(
        db: Session,
        *,
        patient_id: Optional[int] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Payment], int]:
        query = db.query(Payment)
        if patient_id:
            query = query.filter(Payment.patient_id == patient_id)
        if status:
            query = query.filter(Payment.payment_status == status)
        total = query.count()
        return query.offset(skip).limit(limit).all(), total
