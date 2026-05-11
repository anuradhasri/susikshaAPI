# API Examples & Workflows

## Authentication

### 1. Register New User

```bash
POST /api/v1/auth/register
Content-Type: application/json

{
  "username": "newtherapist",
  "email": "therapist@example.com",
  "password": "securepassword123",
  "first_name": "Jane",
  "last_name": "Smith",
  "region_id": 1,
  "phone": "555-9999"
}

Response (201 Created):
{
  "id": 10,
  "username": "newtherapist",
  "email": "therapist@example.com",
  "first_name": "Jane",
  "last_name": "Smith",
  "region_id": 1,
  "phone": "555-9999",
  "is_active": true,
  "is_verified": false,
  "created_at": "2026-05-04T10:30:00",
  "updated_at": "2026-05-04T10:30:00",
  "user_roles": []
}
```

### 2. Login User

```bash
POST /api/v1/auth/login
Content-Type: application/json

{
  "username": "admin",
  "password": "admin123"
}

Response (200 OK):
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### 3. Refresh Access Token

```bash
POST /api/v1/auth/refresh
Content-Type: application/json

{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}

Response (200 OK):
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

## Patient Management

### 1. Create New Patient

```bash
POST /api/v1/patients
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "first_name": "Emma",
  "last_name": "Thompson",
  "date_of_birth": "2015-06-20",
  "gender": "Female",
  "email": "emma.thompson@email.com",
  "phone": "555-5001",
  "address": "123 Main Street, City",
  "diagnosis": "ASD Level 2",
  "medical_history": "Diagnosed at age 4...",
  "emergency_contact": "John Thompson (555-5000)",
  "region_id": 1
}

Response (201 Created):
{
  "id": 42,
  "first_name": "Emma",
  "last_name": "Thompson",
  "date_of_birth": "2015-06-20",
  "gender": "Female",
  "email": "emma.thompson@email.com",
  "phone": "555-5001",
  "address": "123 Main Street, City",
  "diagnosis": "ASD Level 2",
  "medical_history": "Diagnosed at age 4...",
  "emergency_contact": "John Thompson (555-5000)",
  "region_id": 1,
  "created_at": "2026-05-04T10:30:00",
  "updated_at": "2026-05-04T10:30:00"
}
```

### 2. List All Patients

```bash
GET /api/v1/patients?skip=0&limit=10
Authorization: Bearer {access_token}

Response (200 OK):
{
  "total": 25,
  "skip": 0,
  "limit": 10,
  "items": [
    {
      "id": 1,
      "first_name": "Emma",
      "last_name": "Wilson",
      "email": "emma.wilson@email.com",
      "phone": "555-1001",
      "region_id": 1,
      ...
    },
    ...
  ]
}
```

### 3. Get Patient Details

```bash
GET /api/v1/patients/42
Authorization: Bearer {access_token}

Response (200 OK):
{
  "id": 42,
  "first_name": "Emma",
  "last_name": "Thompson",
  "date_of_birth": "2015-06-20",
  "email": "emma.thompson@email.com",
  "phone": "555-5001",
  "diagnosis": "ASD Level 2",
  "region_id": 1,
  ...
}
```

### 4. Update Patient

```bash
PATCH /api/v1/patients/42
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "diagnosis": "ASD Level 2 (Updated)",
  "phone": "555-5002"
}

Response (200 OK):
{
  "id": 42,
  "first_name": "Emma",
  "last_name": "Thompson",
  "diagnosis": "ASD Level 2 (Updated)",
  "phone": "555-5002",
  ...
}
```

### 5. Get Patient Packages

```bash
GET /api/v1/patients/42/packages
Authorization: Bearer {access_token}

Response (200 OK):
[
  {
    "id": 1,
    "patient_id": 42,
    "package_id": 1,
    "start_date": "2026-01-01",
    "end_date": null,
    "sessions_completed": 3,
    "sessions_remaining": 7,
    "status": "active",
    "package": {
      "id": 1,
      "name": "Intensive Therapy Program",
      "total_sessions": 10,
      "price": 1000.0
    }
  }
]
```

## Appointment Management

### 1. Create Appointment

```bash
POST /api/v1/appointments
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "patient_id": 42,
  "therapist_id": 1,
  "start_time": "2026-05-10T10:00:00",
  "end_time": "2026-05-10T11:00:00",
  "status": "scheduled",
  "notes": "Initial assessment session",
  "region_id": 1
}

Response (201 Created):
{
  "id": 150,
  "patient_id": 42,
  "therapist_id": 1,
  "start_time": "2026-05-10T10:00:00",
  "end_time": "2026-05-10T11:00:00",
  "status": "scheduled",
  "notes": "Initial assessment session",
  "region_id": 1,
  "created_at": "2026-05-04T10:30:00",
  "updated_at": "2026-05-04T10:30:00"
}
```

### 2. List Appointments by Date Range

```bash
GET /api/v1/appointments?start=2026-05-01T00:00:00&end=2026-05-31T23:59:59&skip=0&limit=20
Authorization: Bearer {access_token}

Response (200 OK):
{
  "total": 5,
  "skip": 0,
  "limit": 20,
  "items": [
    {
      "id": 150,
      "patient_id": 42,
      "therapist_id": 1,
      "start_time": "2026-05-10T10:00:00",
      "end_time": "2026-05-10T11:00:00",
      "status": "scheduled",
      "region_id": 1,
      ...
    },
    ...
  ]
}
```

### 3. Update Appointment Status

```bash
PATCH /api/v1/appointments/150
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "status": "completed",
  "notes": "Session completed successfully. Patient showed great engagement."
}

Response (200 OK):
{
  "id": 150,
  "status": "completed",
  "notes": "Session completed successfully. Patient showed great engagement.",
  ...
}
```

## Session Management

### 1. Create Session

```bash
POST /api/v1/sessions
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "patient_id": 42,
  "therapist_id": 1,
  "session_number": 5,
  "duration_minutes": 60,
  "status": "scheduled",
  "session_date": "2026-05-10",
  "billing_status": "pending"
}

Response (201 Created):
{
  "id": 350,
  "patient_id": 42,
  "therapist_id": 1,
  "session_number": 5,
  "duration_minutes": 60,
  "status": "scheduled",
  "session_date": "2026-05-10",
  "billing_status": "pending",
  "created_at": "2026-05-04T10:30:00",
  ...
}
```

### 2. Add Session Note

```bash
POST /api/v1/sessions/350/notes
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "note_type": "progress",
  "content": "Patient demonstrated improved communication skills. Worked on turn-taking exercises. Good engagement throughout the session."
}

Response (201 Created):
{
  "id": 1,
  "session_id": 350,
  "note_type": "progress",
  "content": "Patient demonstrated improved communication skills...",
  "created_by": 1,
  "created_at": "2026-05-04T10:30:00",
  ...
}
```

### 3. Complete Session

```bash
POST /api/v1/sessions/350/complete
Authorization: Bearer {access_token}

Response (200 OK):
{
  "id": 350,
  "patient_id": 42,
  "therapist_id": 1,
  "status": "completed",
  "billing_status": "completed",
  "created_at": "2026-05-04T10:30:00",
  ...
}
# Automatically:
# - Updates session status to "completed"
# - Updates billing_status to "completed"
# - Decrements remaining sessions in patient package
```

### 4. List Patient Sessions

```bash
GET /api/v1/sessions?patient_id=42&start_date=2026-01-01&end_date=2026-05-31
Authorization: Bearer {access_token}

Response (200 OK):
{
  "total": 5,
  "skip": 0,
  "limit": 100,
  "items": [
    {
      "id": 346,
      "patient_id": 42,
      "session_number": 1,
      "status": "completed",
      "session_date": "2026-01-05",
      ...
    },
    ...
  ]
}
```

## Billing Operations

### 1. Create Invoice

```bash
POST /api/v1/billing/invoices
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "invoice_number": "INV-2026-001",
  "patient_id": 42,
  "region_id": 1,
  "issue_date": "2026-05-01",
  "due_date": "2026-06-01",
  "total_amount": 500.0,
  "paid_amount": 0,
  "status": "draft",
  "items": [
    {
      "description": "Therapy Sessions (5 sessions)",
      "quantity": 5,
      "unit_price": 100.0,
      "total_price": 500.0
    }
  ]
}

Response (201 Created):
{
  "id": 1,
  "invoice_number": "INV-2026-001",
  "patient_id": 42,
  "region_id": 1,
  "issue_date": "2026-05-01",
  "due_date": "2026-06-01",
  "total_amount": 500.0,
  "paid_amount": 0,
  "status": "draft",
  "items": [
    {
      "id": 1,
      "description": "Therapy Sessions (5 sessions)",
      "quantity": 5,
      "unit_price": 100.0,
      "total_price": 500.0
    }
  ],
  "created_at": "2026-05-04T10:30:00",
  ...
}
```

### 2. Issue Invoice

```bash
POST /api/v1/billing/invoices/1/issue
Authorization: Bearer {access_token}

Response (200 OK):
{
  "id": 1,
  "invoice_number": "INV-2026-001",
  "status": "issued",
  "issue_date": "2026-05-04",
  ...
}
```

### 3. Record Payment

```bash
POST /api/v1/billing/payments
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "invoice_id": 1,
  "amount": 250.0,
  "payment_method": "credit_card",
  "transaction_id": "TXN-12345",
  "notes": "Partial payment received"
}

Response (201 Created):
{
  "id": 1,
  "invoice_id": 1,
  "amount": 250.0,
  "payment_method": "credit_card",
  "transaction_id": "TXN-12345",
  "status": "pending",
  "notes": "Partial payment received",
  "payment_date": null,
  "created_at": "2026-05-04T10:30:00",
  ...
}
```

### 4. Update Payment Status

```bash
PATCH /api/v1/billing/payments/1
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "status": "completed"
}

Response (200 OK):
{
  "id": 1,
  "invoice_id": 1,
  "amount": 250.0,
  "status": "completed",
  "payment_date": "2026-05-04T10:30:00",
  ...
}
# Note: Invoice paid_amount auto-updated
```

## Therapist Management

### 1. List Available Therapists

```bash
GET /api/v1/therapists?is_available=true&skip=0&limit=10
Authorization: Bearer {access_token}

Response (200 OK):
{
  "total": 5,
  "skip": 0,
  "limit": 10,
  "items": [
    {
      "id": 1,
      "user_id": 2,
      "license_number": "LIC-001",
      "specialization": "ASD Spectrum",
      "is_available": true,
      "region_id": 1,
      "user": {
        "id": 2,
        "username": "therapist1",
        "first_name": "Sarah",
        "last_name": "Johnson",
        "email": "therapist1@example.com",
        ...
      },
      ...
    }
  ]
}
```

## Error Examples

### 1. Unauthorized (No Token)

```bash
GET /api/v1/patients
Authorization: 

Response (403 Forbidden):
{
  "detail": "Not authenticated"
}
```

### 2. Region Access Denied

```bash
GET /api/v1/patients/99
Authorization: Bearer {therapist_token_for_region_2}

Response (403 Forbidden):
{
  "detail": "You don't have access to this region"
}
```

### 3. Not Found

```bash
GET /api/v1/patients/9999
Authorization: Bearer {access_token}

Response (404 Not Found):
{
  "detail": "Patient not found"
}
```

### 4. Validation Error

```bash
POST /api/v1/patients
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "first_name": "John",
  "date_of_birth": "invalid-date"
}

Response (422 Unprocessable Entity):
{
  "detail": [
    {
      "loc": ["body", "date_of_birth"],
      "msg": "invalid date format",
      "type": "value_error.date"
    }
  ]
}
```

## Advanced Queries

### Filter Appointments by Therapist

```bash
GET /api/v1/appointments?therapist_id=1&start=2026-05-01T00:00:00&end=2026-05-31T23:59:59
Authorization: Bearer {access_token}
```

### Filter Invoices by Status

```bash
GET /api/v1/billing/invoices?status=issued&patient_id=42
Authorization: Bearer {access_token}
```

### Paginate Results

```bash
GET /api/v1/patients?skip=20&limit=5
# Returns patients 20-24

GET /api/v1/patients?skip=0&limit=100
# Returns first 100 patients
```

## Region-Based Access Examples

### Admin Can See All Regions
```bash
# Admin sees patients from all regions
GET /api/v1/patients
Authorization: Bearer {admin_token}
# Returns 25+ patients from regions 1, 2, 3, 4
```

### Therapist Sees Only Their Region
```bash
# Therapist in region 1 sees only region 1 patients
GET /api/v1/patients
Authorization: Bearer {therapist_region_1_token}
# Returns 8 patients from region 1 only
```

---

For more examples and integration code, see the [README.md](README.md)
