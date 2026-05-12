from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from decimal import Decimal
from datetime import date, datetime
from app.models.models import Invoice, InvoiceItem, Patient, Payment, PaymentModeMaster, Therapist, UserRegionMapping
from app.repositories.payment_repository import PaymentRepository
from app.schemas.schemas import InvoiceCreate, InvoiceUpdate, PaymentCreate, PaymentListRequest
from app.utils.query_utils import soft_delete, filter_by_region


class BillingService:
    """Service for billing operations"""
    
    @staticmethod
    def create_invoice(db: Session, invoice_create: InvoiceCreate) -> Invoice:
        """Create a new invoice"""
        invoice_data = invoice_create.dict(exclude={"items"})
        db_invoice = Invoice(**invoice_data)
        
        db.add(db_invoice)
        db.flush()  # Get invoice ID
        
        # Add invoice items
        total_amount = 0
        for item in invoice_create.items:
            invoice_item = InvoiceItem(
                invoice_id=db_invoice.id,
                **item.dict()
            )
            db.add(invoice_item)
            total_amount += item.total_price
        
        # Update invoice total
        db_invoice.total_amount = total_amount
        
        db.commit()
        db.refresh(db_invoice)
        return db_invoice
    
    @staticmethod
    def get_invoice_by_id(db: Session, invoice_id: int, region_id: int = None) -> Invoice:
        """Get invoice by ID with region filtering"""
        query = db.query(Invoice).filter(
            Invoice.id == invoice_id,
            Invoice.deleted_at.is_(None)
        )
        
        if region_id:
            query = filter_by_region(query, region_id, Invoice)
        
        return query.first()
    
    @staticmethod
    def update_invoice(db: Session, invoice_id: int, invoice_update: InvoiceUpdate, region_id: int = None) -> Invoice:
        """Update invoice"""
        invoice = BillingService.get_invoice_by_id(db, invoice_id, region_id)
        if not invoice:
            return None
        
        for field, value in invoice_update.dict(exclude_unset=True).items():
            setattr(invoice, field, value)
        
        db.commit()
        db.refresh(invoice)
        return invoice
    
    @staticmethod
    def delete_invoice(db: Session, invoice_id: int) -> bool:
        """Soft delete invoice"""
        invoice = soft_delete(db, Invoice, invoice_id)
        return invoice is not None
    
    @staticmethod
    def list_invoices(
        db: Session,
        region_id: int = None,
        patient_id: int = None,
        status: str = None,
        skip: int = 0,
        limit: int = 100
    ) -> tuple:
        """List invoices with filtering"""
        query = db.query(Invoice).filter(Invoice.deleted_at.is_(None))
        
        if region_id:
            query = filter_by_region(query, region_id, Invoice)
        
        if patient_id:
            query = query.filter(Invoice.patient_id == patient_id)
        
        if status:
            query = query.filter(Invoice.status == status)
        
        total = query.count()
        invoices = query.offset(skip).limit(limit).all()
        
        return invoices, total
    
    @staticmethod
    def mark_as_issued(db: Session, invoice_id: int) -> Invoice:
        """Mark invoice as issued"""
        invoice = BillingService.get_invoice_by_id(db, invoice_id)
        if invoice and invoice.status == "draft":
            invoice.status = "issued"
            invoice.issue_date = date.today()
            db.commit()
            db.refresh(invoice)
        return invoice


class PaymentService:
    """Service for payment operations"""
    
    @staticmethod
    def record_payment(db: Session, payment_create: PaymentCreate) -> Payment:
        """Record a payment"""
        payment = PaymentService.add_payment(db, payment_create)
        db.commit()
        db.refresh(payment)
        return payment
    
    @staticmethod
    def get_payment_by_id(db: Session, payment_id: int) -> Payment:
        """Get payment by ID"""
        return PaymentRepository.get_by_id(db, payment_id)
    
    @staticmethod
    def update_payment_status(db: Session, payment_id: int, status: str) -> Payment:
        """Update payment status"""
        payment = PaymentService.get_payment_by_id(db, payment_id)
        if not payment:
            return None
        
        payment.status = status
        if status == "completed":
            payment.payment_date = date.today()
        
        db.commit()
        db.refresh(payment)
        return payment
    
    # fetch payments list
    
    @staticmethod
    def list_payments(
        db: Session,
        request: PaymentListRequest,
        created_by: int
    ):

        # Get User Region IDs
        user_regions = (
            db.query(UserRegionMapping.regionid)
            .filter(UserRegionMapping.userid == created_by)
            .all()
        )

        region_ids = [region.regionid for region in user_regions]

        query = (
            db.query(
                Payment.id.label("payment_id"),

                Patient.first_name.label("first_name"),
                Patient.last_name.label("last_name"),

                func.concat(
                    Patient.first_name,
                    " ",
                    Patient.last_name
                ).label("full_name"),

                PaymentModeMaster.payment_mode_name.label("payment_mode"),

                Payment.payment_amount.label("payment_amount"),

                Payment.payment_status.label("payment_status"),

                Payment.remark.label("payment_remark"),

                Payment.payment_date.label("payment_date")
            )
            .join(
                Patient,
                Patient.id == Payment.patient_id
            )
            .outerjoin(
                PaymentModeMaster,
                PaymentModeMaster.id == Payment.payment_mode
            )

            # REGION FILTER
            .filter(
                Patient.region_id.in_(region_ids)
            )
        )

        # Additional Filters

        if request.patient_id:
            query = query.filter(
                Payment.patient_id == request.patient_id
            )

        if request.payment_status:
            query = query.filter(
                Payment.payment_status == request.payment_status
            )

        if request.payment_mode_id:
            query = query.filter(
                Payment.payment_mode == request.payment_mode_id
            )

        print(
            query.statement.compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        total = query.count()
        payments = (
            query
            .offset(request.skip)
            .limit(request.limit)
            .all()
        )

        return payments, total

#  add payment

    @staticmethod
    def add_payment(
        db: Session,
        payment_create: PaymentCreate,
        created_by: int
    ) -> Payment:

        try:

            # Validate Amount
            if payment_create.payment_amount <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Amount must be greater than zero"
                )

            payment_status = payment_create.payment_status or (
                "PAID" if payment_create.payment_mode is not None else "PENDING"
            )

            saved_payment_date = (
                payment_create.payment_date
                or datetime.utcnow()
            )
            
            
            # Create Payment Object
            payment = Payment(
                patient_id=payment_create.patient_id,
                payment_amount=payment_create.payment_amount,
                payment_mode=payment_create.payment_mode,
                payment_status=payment_status,
                remark=payment_create.remark,
                payment_date=saved_payment_date,
                created_by=created_by
            )

            db.add(payment)
            db.commit()
            db.refresh(payment)

            return payment

        except HTTPException as http_ex:
            db.rollback()
            raise http_ex

        except Exception as e:
            db.rollback()
            raise Exception(str(e))

class TherapistService:
    """Service for therapist operations"""
    
    @staticmethod
    def get_therapist_by_id(db: Session, therapist_id: int, region_id: int = None) -> Therapist:
        """Get therapist by ID"""
        query = db.query(Therapist).filter(
            Therapist.id == therapist_id,
            Therapist.deleted_at.is_(None)
        )
        
        if region_id:
            query = filter_by_region(query, region_id, Therapist)
        
        return query.first()
    
    @staticmethod
    def list_therapists(
        db: Session,
        region_id: int = None,
        is_available: bool = None,
        skip: int = 0,
        limit: int = 100
    ) -> tuple:
        """List therapists with filtering"""
        query = db.query(Therapist).filter(Therapist.deleted_at.is_(None))
        
        if region_id:
            query = filter_by_region(query, region_id, Therapist)
        
        if is_available is not None:
            query = query.filter(Therapist.is_available == is_available)
        
        total = query.count()
        therapists = query.offset(skip).limit(limit).all()
        
        return therapists, total

