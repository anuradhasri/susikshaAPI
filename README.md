# Autism Therapy Management System - Backend

A production-ready backend system for managing autism therapy operations, including patient management, therapist scheduling, sessions, billing, and comprehensive administrative controls.

## 🚀 Tech Stack

- **Framework**: FastAPI with Python 3.8+
- **Database**: MySQL with SQLAlchemy ORM
- **Caching & Background Jobs**: Redis
- **Server**: Uvicorn (ASGI) + Gunicorn (Process Manager with 10 workers)
- **Authentication**: JWT-based with Role-Based Access Control (RBAC)
- **API Documentation**: Swagger UI

## 📁 Project Structure

```
autism-backend/
├── app/
│   ├── main.py                 # FastAPI application entry point
│   ├── core/
│   │   ├── config.py          # Configuration settings
│   │   ├── database.py        # Database setup
│   │   ├── redis_client.py    # Redis configuration
│   │   └── security.py        # JWT and password hashing
│   ├── models/
│   │   └── models.py          # SQLAlchemy ORM models
│   ├── schemas/
│   │   └── schemas.py         # Pydantic validation schemas
│   ├── api/routes/
│   │   ├── auth.py            # Authentication endpoints
│   │   ├── patients.py        # Patient management
│   │   ├── therapists.py      # Therapist management
│   │   ├── appointments.py    # Appointment scheduling
│   │   ├── sessions.py        # Session management
│   │   └── billing.py         # Invoices and payments
│   ├── services/
│   │   ├── user_service.py    # User business logic
│   │   ├── patient_service.py # Patient operations
│   │   ├── appointment_service.py  # Appointment/Session logic
│   │   └── billing_service.py      # Billing operations
│   ├── dependencies/
│   │   └── auth.py            # Dependency injection for auth
│   ├── middleware/
│   │   └── middleware.py      # Custom middleware (region filtering, logging)
│   └── utils/
│       ├── logger.py          # JSON structured logging
│       └── query_utils.py     # Query helpers and duplicate detection
├── gunicorn_conf.py           # Gunicorn configuration (10 workers)
├── requirements.txt           # Python dependencies
├── .env.example               # Environment variables template
└── README.md                  # This file
```

## 🔐 Authentication & Authorization

### User Roles

1. **Admin**
   - Full access to ALL data across ALL regions
   - Can manage users, roles, and system configuration

2. **Therapist**
   - Can only access their assigned patients and sessions
   - Can only see data within their region
   - Limited to viewing their own appointments and billing

3. **Front Office**
   - Can access patients, appointments, and billing
   - Limited to their assigned region
   - Cannot access therapist-specific information

### Region-Based Access Control

Every user belongs to a `region_id`. Non-admin users can only access data where `region_id = user.region_id`.

This is enforced at:
- **Middleware Level**: `RegionAccessMiddleware` filters tokens
- **Query Level**: All queries include region filtering using `filter_by_region()`
- **Dependency Level**: `check_region_access()` validates access before operations

## 🧱 Database Models

### Core Tables

- **users**: System users with authentication
- **roles**: User roles (admin, therapist, front_office)
- **user_roles**: Many-to-many relationship between users and roles
- **regions**: Geographic regions for data isolation

### Patient & Therapy

- **patients**: Patient demographics and medical history
- **therapists**: Therapist profiles linked to users
- **appointments**: Scheduled appointments between therapists and patients
- **sessions**: Completed therapy sessions with progress tracking
- **session_notes**: Detailed notes on each session
- **session_assignments**: Links sessions to service packages

### Packages & Billing

- **packages**: Predefined therapy packages (e.g., 10 sessions for $500)
- **patient_packages**: Patient enrollment in therapy packages
- **invoices**: Billing documents
- **invoice_items**: Line items on invoices
- **payments**: Payment records

### Administrative

- **notifications**: User notifications
- **alerts**: System alerts (revenue drops, session leakage)
- **documents**: Patient documents (assessments, consent forms)
- **audit_logs**: Complete audit trail of all changes
- **patient_duplicates**: Detected potential duplicate patients

## ⚡ Key Features

### 1. Appointment Management
- **Check therapist availability** before booking
- **Validate patient has available sessions** in their package
- **Automatic slot conflict detection**
- List appointments with date range filtering

```python
# Example: Create appointment with validation
POST /api/v1/appointments
{
  "patient_id": 1,
  "therapist_id": 2,
  "start_time": "2026-05-10T10:00:00",
  "end_time": "2026-05-10T11:00:00",
  "region_id": 1
}
```

### 2. Session Management
- **Auto-incrementing session numbers**
- **Progress note tracking**
- **Automatic billing status updates**
- **Package session consumption tracking**

```python
# Example: Complete session
POST /api/v1/sessions/{session_id}/complete
# Automatically updates patient package session count
```

### 3. Duplicate Patient Detection
- **Similarity-based detection** using string matching
- **Configurable threshold** (default 85%)
- **Matches on**: first name, last name, email, phone
- **Stores matches in patient_duplicates table**

```python
# Automatic: When creating patient
POST /api/v1/patients
{
  "first_name": "John",
  "last_name": "Doe",
  "email": "john@example.com",
  "phone": "555-1234",
  "date_of_birth": "2010-01-15",
  "region_id": 1
}
# System automatically checks for duplicates
```

### 4. Billing System
- **Invoice generation** with line items
- **Payment tracking** with multiple payment methods
- **Automatic payment allocation** to invoices
- **Status management** (draft → issued → paid)

```python
# Example: Create invoice
POST /api/v1/billing/invoices
{
  "invoice_number": "INV-001",
  "patient_id": 1,
  "region_id": 1,
  "issue_date": "2026-05-01",
  "due_date": "2026-06-01",
  "items": [
    {
      "description": "Therapy Session",
      "quantity": 10,
      "unit_price": 100.0,
      "total_price": 1000.0
    }
  ]
}
```

### 5. Redis Integration
- **Caching**: Appointments cached for 5 minutes
- **Background Jobs**: Duplicate detection, alerts
- **Session Management**: Recent sessions stored in Redis
- **Performance Optimization**: Reduced database queries

### 6. Comprehensive Logging
- **JSON-structured logging** for easy parsing
- **Audit trail** of all data changes
- **Request/response logging** with timing
- **User activity tracking**

## 🔐 API Endpoints

### Authentication

```
POST   /api/v1/auth/register          # Register new user
POST   /api/v1/auth/login             # Login and get tokens
POST   /api/v1/auth/refresh           # Refresh access token
```

### Patients

```
GET    /api/v1/patients               # List patients (with region filtering)
POST   /api/v1/patients               # Create patient
GET    /api/v1/patients/{id}          # Get patient details
PATCH  /api/v1/patients/{id}          # Update patient
DELETE /api/v1/patients/{id}          # Delete (soft delete)
GET    /api/v1/patients/{id}/packages # Get patient's packages
```

### Appointments

```
GET    /api/v1/appointments?start=&end=  # List with date filtering
POST   /api/v1/appointments               # Create appointment
GET    /api/v1/appointments/{id}         # Get appointment
PATCH  /api/v1/appointments/{id}        # Update appointment
DELETE /api/v1/appointments/{id}        # Delete appointment
```

### Sessions

```
GET    /api/v1/sessions?patient_id=  # List sessions
POST   /api/v1/sessions              # Create session
GET    /api/v1/sessions/{id}         # Get session
PATCH  /api/v1/sessions/{id}         # Update session
POST   /api/v1/sessions/{id}/notes   # Add session note
POST   /api/v1/sessions/{id}/complete# Mark as completed
```

### Therapists

```
GET    /api/v1/therapists            # List therapists
GET    /api/v1/therapists/{id}       # Get therapist
```

### Billing

```
GET    /api/v1/billing/invoices              # List invoices
POST   /api/v1/billing/invoices              # Create invoice
GET    /api/v1/billing/invoices/{id}         # Get invoice
PATCH  /api/v1/billing/invoices/{id}         # Update invoice
POST   /api/v1/billing/invoices/{id}/issue   # Issue invoice
DELETE /api/v1/billing/invoices/{id}         # Delete invoice

GET    /api/v1/billing/payments              # List payments
POST   /api/v1/billing/payments              # Record payment
GET    /api/v1/billing/payments/{id}         # Get payment
PATCH  /api/v1/billing/payments/{id}         # Update payment status
```

## 🔥 Region-Based Query Filtering

All queries automatically filter by user's region unless user is admin.

### Example: Query Filtering in Practice

```python
# Therapist queries their patients (auto-filtered by region)
GET /api/v1/patients
# Returns only patients in therapist's region

# Admin queries same endpoint
GET /api/v1/patients
# Returns patients from all regions (if any region_id override used)

# Implementation in service:
query = db.query(Patient).filter(Patient.deleted_at.is_(None))
if region_id:
    query = filter_by_region(query, region_id, Patient)
patients = query.all()
```

## 💾 Soft Deletes

All tables include `deleted_at` timestamp. Deletes don't remove data permanently:

```python
# All queries automatically exclude soft-deleted records
query = db.query(User).filter(User.deleted_at.is_(None))

# When deleting:
user.deleted_at = datetime.utcnow()
db.commit()
```

## 🚀 Installation & Setup

### Prerequisites

- Python 3.8+
- MySQL 5.7+
- Redis 6.0+

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Environment Setup

```bash
cp .env.example .env
# Edit .env with your configuration
```

### 3. Database Setup

```bash
# Create MySQL database
mysql -u root -p
CREATE DATABASE autism_therapy;
```

### 4. Run Application

**Development:**
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Production with Gunicorn (10 workers):**
```bash
gunicorn -c gunicorn_conf.py app.main:app
```

### 5. Access Documentation

Open your browser to:
- **Swagger UI**: http://localhost:8000/api/docs
- **ReDoc**: http://localhost:8000/api/redoc

## 📊 Example Workflows

### 1. Complete Appointment Booking Workflow

```python
# 1. Register/login
POST /api/v1/auth/login
{
  "username": "therapist1",
  "password": "password"
}
# Returns: access_token, refresh_token

# 2. Get available therapists
GET /api/v1/therapists?is_available=true
# Returns: list of available therapists

# 3. Check patient has available sessions
GET /api/v1/patients/1/packages
# Returns: patient's active packages and remaining sessions

# 4. Create appointment
POST /api/v1/appointments
{
  "patient_id": 1,
  "therapist_id": 2,
  "start_time": "2026-05-10T10:00:00",
  "end_time": "2026-05-10T11:00:00",
  "region_id": 1
}
```

### 2. Session Completion with Billing

```python
# 1. Create session
POST /api/v1/sessions
{
  "patient_id": 1,
  "therapist_id": 2,
  "session_number": 5,
  "duration_minutes": 60,
  "session_date": "2026-05-10"
}

# 2. Add progress notes
POST /api/v1/sessions/1/notes
{
  "note_type": "progress",
  "content": "Patient showed improvement in communication..."
}

# 3. Complete session
POST /api/v1/sessions/1/complete
# Automatically:
# - Updates session status to "completed"
# - Increments session count in patient package
# - Decrements remaining sessions

# 4. Create invoice
POST /api/v1/billing/invoices
{
  "invoice_number": "INV-005",
  "patient_id": 1,
  "region_id": 1,
  "issue_date": "2026-05-10",
  "due_date": "2026-06-10",
  "items": [{"description": "Therapy Session", "quantity": 1, "unit_price": 100, "total_price": 100}]
}

# 5. Record payment
POST /api/v1/billing/payments
{
  "invoice_id": 5,
  "amount": 100,
  "payment_method": "credit_card"
}
```

### 3. Duplicate Patient Detection

```python
# When creating patient with similar data:
POST /api/v1/patients
{
  "first_name": "John",
  "last_name": "Doe",
  "email": "john.doe@example.com",
  "phone": "555-1234",
  "date_of_birth": "2010-01-15",
  "region_id": 1
}

# System automatically:
# 1. Checks for similarities with existing patients
# 2. If similarity > 85%, creates entry in patient_duplicates
# 3. Returns patient record with duplicate indicators
```

## 🔒 Security Features

1. **JWT Authentication**: Secure token-based authentication
2. **Password Hashing**: Bcrypt with salt
3. **Role-Based Access Control**: Fine-grained permissions
4. **Region-Based Access**: Data isolation by region
5. **Audit Logging**: Complete audit trail
6. **SQL Injection Protection**: SQLAlchemy parameterized queries
7. **CORS Configuration**: Configurable allowed origins
8. **HTTP-only Cookies**: Secure token storage (can be implemented)

## 🧪 Testing & Validation

### Example Tests

```python
# Test region filtering
def test_therapist_region_filtering():
    """Therapists should only see patients in their region"""
    response = client.get(
        "/api/v1/patients",
        headers={"Authorization": f"Bearer {therapist_token}"}
    )
    assert all(p["region_id"] == therapist_region_id for p in response.json()["items"])

# Test admin access
def test_admin_cross_region_access():
    """Admins should see all patients"""
    response = client.get(
        "/api/v1/patients",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    # Should contain patients from multiple regions
    regions = {p["region_id"] for p in response.json()["items"]}
    assert len(regions) > 1
```

## 📈 Performance Considerations

1. **Connection Pooling**: Database connections pooled (pool_size=10, max_overflow=20)
2. **Redis Caching**: 5-minute TTL on appointment caches
3. **Index Optimization**: Strategic indexes on frequently queried fields
4. **Gunicorn Workers**: 10 workers for concurrent request handling
5. **Query Optimization**: Efficient filtering and joins
6. **Soft Deletes**: Prevents data bloat while maintaining audit trail

## 🚨 Monitoring & Alerts

### Implemented Alerts

- Revenue drop detection
- Session leakage tracking
- Duplicate patient identification
- Payment overdue notifications
- System health checks

### Logging

All operations logged to JSON for easy parsing:
```json
{
  "timestamp": "2026-05-04T10:30:00",
  "level": "INFO",
  "logger": "app.services.appointment_service",
  "message": "Appointment created",
  "appointment_id": 123,
  "user_id": 5,
  "patient_id": 42
}
```

## 📝 API Response Format

All responses follow a consistent format:

**Success (200):**
```json
{
  "id": 1,
  "name": "John Doe",
  "email": "john@example.com",
  "created_at": "2026-05-04T10:30:00",
  "updated_at": "2026-05-04T10:30:00"
}
```

**Error (400/404/500):**
```json
{
  "detail": "Patient not found"
}
```

**Paginated (200):**
```json
{
  "total": 100,
  "skip": 0,
  "limit": 10,
  "items": [...]
}
```

## 🔧 Configuration

### Environment Variables

See `.env.example` for all configuration options:

- `DATABASE_URL`: MySQL connection string
- `REDIS_URL`: Redis connection string
- `SECRET_KEY`: JWT signing key (change in production!)
- `ACCESS_TOKEN_EXPIRE_MINUTES`: Token expiration time
- `LOG_LEVEL`: Logging verbosity
- `CORS_ORIGINS`: Allowed origins for CORS

## 🛠️ Development

### Adding New Endpoints

1. Create schema in `app/schemas/schemas.py`
2. Create service method in appropriate `app/services/*.py`
3. Create route in `app/api/routes/*.py`
4. Add region filtering if applicable
5. Add proper error handling and logging
6. Document in README

### Adding New Model

1. Define model in `app/models/models.py`
2. Include: id, created_at, updated_at, deleted_at
3. Add indexes for frequently queried fields
4. Add relationships to related models
5. Create corresponding schema and service

## 📚 Additional Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/)
- [Pydantic](https://pydantic-settings.readthedocs.io/)
- [Redis Documentation](https://redis.io/documentation)
- [Gunicorn Configuration](https://docs.gunicorn.org/en/stable/configure.html)

## 📄 License

[Add your license information here]

## 👥 Support

For issues and questions:
1. Check API documentation at `/api/docs`
2. Review logs for detailed error information
3. Check database for data consistency
4. Validate JWT tokens and region access

---

**Version**: 1.0.0  
**Last Updated**: May 4, 2026  
**Built with**: FastAPI, SQLAlchemy, Redis, MySQL
