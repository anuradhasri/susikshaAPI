from sqlalchemy.orm import Session
from decimal import Decimal
from datetime import date
from app.models.models import Invoice, InvoiceItem, Payment, Therapist
from app.schemas.schemas import InvoiceCreate, InvoiceUpdate, PaymentCreate
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
        payment = Payment(**payment_create.dict())
        payment.status = "pending"
        
        db.add(payment)
        db.commit()
        db.refresh(payment)
        
        # Update invoice paid amount
        invoice = db.query(Invoice).filter(Invoice.id == payment.invoice_id).first()
        if invoice:
            invoice.paid_amount += payment.amount
            
            if invoice.paid_amount >= invoice.total_amount:
                invoice.status = "paid"
            
            db.commit()
        
        return payment
    
    @staticmethod
    def get_payment_by_id(db: Session, payment_id: int) -> Payment:
        """Get payment by ID"""
        return db.query(Payment).filter(
            Payment.id == payment_id,
            Payment.deleted_at.is_(None)
        ).first()
    
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
    
    @staticmethod
    def list_payments(
        db: Session,
        invoice_id: int = None,
        status: str = None,
        skip: int = 0,
        limit: int = 100
    ) -> tuple:
        """List payments with filtering"""
        query = db.query(Payment).filter(Payment.deleted_at.is_(None))
        
        if invoice_id:
            query = query.filter(Payment.invoice_id == invoice_id)
        
        if status:
            query = query.filter(Payment.status == status)
        
        total = query.count()
        payments = query.offset(skip).limit(limit).all()
        
        return payments, total


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
