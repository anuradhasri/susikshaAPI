from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies.auth import get_current_user, check_region_access
from app.schemas.schemas import InvoiceCreate, InvoiceUpdate, InvoiceResponse, PaginatedPaymentResponse, PaymentCreate, PaymentListRequest, PaymentResponse, PaginatedResponse
from app.services.billing_service import BillingService, PaymentService
from app.models.models import User
from app.utils.logger import setup_logging

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
logger = setup_logging(__name__)


# ============== INVOICES ==============

@router.post("/invoices", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    invoice_create: InvoiceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new invoice"""
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=invoice_create.region_id)
    
    try:
        invoice = BillingService.create_invoice(db, invoice_create)
        logger.info(
            f"Invoice created",
            extra={
                "invoice_id": invoice.id,
                "user_id": current_user.id,
                "patient_id": invoice.patient_id
            }
        )
        return invoice
    except Exception as e:
        logger.error(f"Error creating invoice: {str(e)}", extra={"user_id": current_user.id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating invoice"
        )


@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get invoice by ID"""
    invoice = BillingService.get_invoice_by_id(db, invoice_id, current_user.region_id)
    
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=invoice.region_id)
    
    return invoice


@router.patch("/invoices/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: int,
    invoice_update: InvoiceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update invoice"""
    invoice = BillingService.get_invoice_by_id(db, invoice_id, current_user.region_id)
    
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=invoice.region_id)
    
    try:
        updated_invoice = BillingService.update_invoice(db, invoice_id, invoice_update, current_user.region_id)
        logger.info(
            f"Invoice updated",
            extra={"invoice_id": invoice_id, "user_id": current_user.id}
        )
        return updated_invoice
    except Exception as e:
        logger.error(f"Error updating invoice: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating invoice"
        )


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete invoice"""
    invoice = BillingService.get_invoice_by_id(db, invoice_id, current_user.region_id)
    
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=invoice.region_id)
    
    try:
        BillingService.delete_invoice(db, invoice_id)
        logger.info(
            f"Invoice deleted",
            extra={"invoice_id": invoice_id, "user_id": current_user.id}
        )
    except Exception as e:
        logger.error(f"Error deleting invoice: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error deleting invoice"
        )


@router.get("/invoices", response_model=PaginatedResponse)
async def list_invoices(
    patient_id: int = Query(None),
    status: str = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List invoices"""
    invoices, total = BillingService.list_invoices(
        db,
        region_id=current_user.region_id,
        patient_id=patient_id,
        status=status,
        skip=skip,
        limit=limit
    )
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": invoices
    }


@router.post("/invoices/{invoice_id}/issue", response_model=InvoiceResponse)
async def issue_invoice(
    invoice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark invoice as issued"""
    invoice = BillingService.get_invoice_by_id(db, invoice_id, current_user.region_id)
    
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found"
        )
    
    # Check region access
    await check_region_access(current_user=current_user, db=db, target_region_id=invoice.region_id)
    
    try:
        issued_invoice = BillingService.mark_as_issued(db, invoice_id)
        logger.info(
            f"Invoice marked as issued",
            extra={"invoice_id": invoice_id, "user_id": current_user.id}
        )
        return issued_invoice
    except Exception as e:
        logger.error(f"Error issuing invoice: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error issuing invoice"
        )


# ============== PAYMENTS ==============

# add payment

@router.post("/payments", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def add_payment(
    payment_create: PaymentCreate,
    current_user: User = Depends(get_current_user),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    try:

        payment = PaymentService.add_payment(
            db=db,
            payment_create=payment_create,        
            created_by=current_user.id
        )

        logger.info(
            "Payment recorded successfully",
            extra={
                "payment_id": payment.id,
                "patient_id": payment.patient_id,
                "user_id": current_user.id
            }
        )

        return payment

    except HTTPException as http_ex:
        raise http_ex

    except Exception as e:

        logger.error(
            f"Error recording payment: {str(e)}",
            extra={
                "user_id": current_user.id
            }
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record payment"
        )

@router.get(
    "/payments/list",
    response_model=PaginatedPaymentResponse
)
async def list_payments(
    request: PaymentListRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    payments, total = PaymentService.list_payments(
        db=db,
        request=request,
        created_by=current_user.id
    )

    return {
        "total": total,
        "skip": request.skip,
        "limit": request.limit,
        "items": payments
    }

@router.post("/payments", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def record_payment(
    payment_create: PaymentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Record a payment"""
    # Get invoice and check region access
    from app.models.models import Invoice
    invoice = db.query(Invoice).filter(Invoice.id == payment_create.invoice_id).first()
    
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found"
        )
    
    # Check region access
    await check_region_access(current_user, invoice.region_id)
    
    try:
        payment = PaymentService.record_payment(db, payment_create)
        logger.info(
            f"Payment recorded",
            extra={
                "payment_id": payment.id,
                "user_id": current_user.id,
                "invoice_id": invoice.id
            }
        )
        return payment
    except Exception as e:
        logger.error(f"Error recording payment: {str(e)}", extra={"user_id": current_user.id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error recording payment"
        )


@router.get("/payments/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get payment by ID"""
    payment = PaymentService.get_payment_by_id(db, payment_id)
    
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found"
        )
    
    return payment


@router.patch("/payments/{payment_id}", response_model=PaymentResponse)
async def update_payment_status(
    payment_id: int,
    new_status: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update payment status"""
    payment = PaymentService.get_payment_by_id(db, payment_id)
    
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found"
        )
    
    try:
        updated_payment = PaymentService.update_payment_status(db, payment_id, new_status)
        logger.info(
            f"Payment status updated",
            extra={"payment_id": payment_id, "user_id": current_user.id, "status": new_status}
        )
        return updated_payment
    except Exception as e:
        logger.error(f"Error updating payment: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error updating payment"
        )


@router.get("/payments", response_model=PaginatedResponse)
async def list_payments(
    patient_id: int = Query(None),
    status: str = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    # current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List payments"""
    payments, total = PaymentService.list_payments(
        db,
        # invoice_id=invoice_id,
        status=status,
        skip=skip,
        limit=limit
    )
    if patient_id:
        payments = [payment for payment in payments if payment.patient_id == patient_id]
        total = len(payments)
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": payments
    }
