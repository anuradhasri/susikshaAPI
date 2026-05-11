from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field


# ============== USER SCHEMAS ==============

class RoleBase(BaseModel):
    name: str
    description: Optional[str] = None


class RoleCreate(RoleBase):
    pass


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class RoleResponse(RoleBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserRoleResponse(BaseModel):
    id: int
    role_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class UserBase(BaseModel):
    username: str
    email: EmailStr
    first_name: str
    last_name: str
    region_id: int
    phone: Optional[str] = None


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(UserBase):
    id: int
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime
    user_roles: List[RoleResponse] = []

    class Config:
        from_attributes = True


class UserResponseWithRoles(UserResponse):
    user_roles: List[RoleResponse]


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"


# ============== REGION SCHEMAS ==============

class RegionBase(BaseModel):
    name: str
    code: str
    location: Optional[str] = None


class RegionCreate(RegionBase):
    pass


class RegionUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    location: Optional[str] = None


class RegionResponse(RegionBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============== PATIENT SCHEMAS ==============

class PatientBase(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date
    gender: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    diagnosis: Optional[str] = None
    medical_history: Optional[str] = None
    emergency_contact: Optional[str] = None
    region_id: int


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    diagnosis: Optional[str] = None
    medical_history: Optional[str] = None
    emergency_contact: Optional[str] = None


class PatientResponse(PatientBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============== THERAPIST SCHEMAS ==============

class TherapistBase(BaseModel):
    license_number: str
    specialization: Optional[str] = None
    qualification: Optional[str] = None
    is_available: bool = True
    region_id: int


class TherapistCreate(TherapistBase):
    user_id: int


class TherapistUpdate(BaseModel):
    license_number: Optional[str] = None
    specialization: Optional[str] = None
    qualification: Optional[str] = None
    is_available: Optional[bool] = None


class TherapistResponse(TherapistBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    user: UserResponse

    class Config:
        from_attributes = True


# ============== APPOINTMENT SCHEMAS ==============

class AppointmentBase(BaseModel):
    patient_id: int
    therapist_id: int
    start_time: datetime
    end_time: datetime
    status: Optional[str] = "scheduled"
    notes: Optional[str] = None
    region_id: int


class AppointmentCreate(AppointmentBase):
    pass


class AppointmentUpdate(BaseModel):
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class AppointmentResponse(AppointmentBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AppointmentDetailResponse(AppointmentResponse):
    patient: PatientResponse
    therapist: TherapistResponse


# ============== SESSION SCHEMAS ==============

class SessionBase(BaseModel):
    patient_id: int
    therapist_id: int
    session_number: int
    duration_minutes: int
    status: Optional[str] = "scheduled"
    session_date: date
    progress_notes: Optional[str] = None
    billing_status: Optional[str] = "pending"


class SessionCreate(SessionBase):
    appointment_id: Optional[int] = None


class SessionUpdate(BaseModel):
    status: Optional[str] = None
    progress_notes: Optional[str] = None
    billing_status: Optional[str] = None


class SessionResponse(SessionBase):
    id: int
    appointment_id: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============== SESSION NOTE SCHEMAS ==============

class SessionNoteBase(BaseModel):
    session_id: int
    note_type: str
    content: str


class SessionNoteCreate(SessionNoteBase):
    created_by: int


class SessionNoteResponse(SessionNoteBase):
    id: int
    created_by: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============== PACKAGE SCHEMAS ==============

class PackageBase(BaseModel):
    name: str
    description: Optional[str] = None
    total_sessions: int
    price: float
    duration_days: Optional[int] = None
    is_active: bool = True


class PackageCreate(PackageBase):
    pass


class PackageUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    total_sessions: Optional[int] = None
    price: Optional[float] = None
    duration_days: Optional[int] = None
    is_active: Optional[bool] = None


class PackageResponse(PackageBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============== PATIENT PACKAGE SCHEMAS ==============

class PatientPackageBase(BaseModel):
    patient_id: int
    package_id: int
    start_date: date
    end_date: Optional[date] = None
    sessions_completed: int = 0
    sessions_remaining: int
    status: Optional[str] = "active"


class PatientPackageCreate(PatientPackageBase):
    pass


class PatientPackageUpdate(BaseModel):
    status: Optional[str] = None
    sessions_completed: Optional[int] = None
    sessions_remaining: Optional[int] = None
    end_date: Optional[date] = None


class PatientPackageResponse(PatientPackageBase):
    id: int
    created_at: datetime
    updated_at: datetime
    package: PackageResponse

    class Config:
        from_attributes = True


# ============== INVOICE SCHEMAS ==============

class InvoiceItemBase(BaseModel):
    description: str
    quantity: int
    unit_price: float
    total_price: float


class InvoiceItemCreate(InvoiceItemBase):
    pass


class InvoiceItemResponse(InvoiceItemBase):
    id: int
    invoice_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class InvoiceBase(BaseModel):
    invoice_number: str
    patient_id: int
    region_id: int
    issue_date: date
    due_date: date
    total_amount: float
    paid_amount: float = 0
    status: Optional[str] = "draft"
    description: Optional[str] = None


class InvoiceCreate(InvoiceBase):
    items: List[InvoiceItemCreate] = []


class InvoiceUpdate(BaseModel):
    total_amount: Optional[float] = None
    paid_amount: Optional[float] = None
    status: Optional[str] = None
    due_date: Optional[date] = None


class InvoiceResponse(InvoiceBase):
    id: int
    created_at: datetime
    updated_at: datetime
    items: List[InvoiceItemResponse] = []

    class Config:
        from_attributes = True


# ============== PAYMENT SCHEMAS ==============

class PaymentBase(BaseModel):
    invoice_id: int
    amount: float
    payment_method: str
    transaction_id: Optional[str] = None
    notes: Optional[str] = None


class PaymentCreate(PaymentBase):
    pass


class PaymentUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


class PaymentResponse(PaymentBase):
    id: int
    status: str
    payment_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============== NOTIFICATION SCHEMAS ==============

class NotificationBase(BaseModel):
    user_id: int
    title: str
    message: str
    notification_type: str


class NotificationResponse(NotificationBase):
    id: int
    is_read: bool
    data: Optional[dict]
    created_at: datetime

    class Config:
        from_attributes = True


# ============== ALERT SCHEMAS ==============

class AlertBase(BaseModel):
    alert_type: str
    title: str
    description: str
    severity: str
    patient_id: Optional[int] = None


class AlertCreate(AlertBase):
    pass


class AlertResponse(AlertBase):
    id: int
    is_active: bool
    metadata: Optional[dict]
    created_at: datetime

    class Config:
        from_attributes = True


# ============== DOCUMENT SCHEMAS ==============

class DocumentBase(BaseModel):
    patient_id: int
    document_type: str
    title: str
    file_path: str
    file_size: int
    mime_type: str
    description: Optional[str] = None
    is_confidential: bool = False


class DocumentCreate(DocumentBase):
    uploaded_by: int


class DocumentResponse(DocumentBase):
    id: int
    uploaded_by: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============== DUPLICATE DETECTION SCHEMAS ==============

class PatientDuplicateResponse(BaseModel):
    id: int
    patient_id_1: int
    patient_id_2: int
    similarity_score: float
    matched_fields: dict
    status: str
    reviewed_by: Optional[int]
    reviewed_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# ============== PAGINATION SCHEMAS ==============

class PaginationParams(BaseModel):
    skip: int = 0
    limit: int = 100


class PaginatedResponse(BaseModel):
    total: int
    skip: int
    limit: int
    items: List
