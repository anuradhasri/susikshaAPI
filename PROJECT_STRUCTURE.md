# Project Structure & File Reference

## Complete Directory Tree

```
autism-backend/
│
├── app/
│   ├── __init__.py                          # Package initializer
│   ├── main.py                              # FastAPI application entry point
│   │                                        # - Creates FastAPI instance
│   │                                        # - Adds CORS middleware
│   │                                        # - Adds custom middleware
│   │                                        # - Includes all routers
│   │                                        # - Defines health check endpoint
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                        # Configuration settings
│   │   │   │                                # - App settings
│   │   │   │                                # - Database URL
│   │   │   │                                # - Redis URL
│   │   │   │                                # - JWT settings
│   │   │   │                                # - CORS configuration
│   │   │   └─ get_settings() function
│   │   │
│   │   ├── database.py                      # Database configuration
│   │   │   │                                # - SQLAlchemy engine
│   │   │   │                                # - Session factory
│   │   │   │                                # - Connection pooling
│   │   │   └─ init_db() function
│   │   │
│   │   ├── redis_client.py                  # Redis configuration
│   │   │   │                                # - Redis connection
│   │   │   │                                # - Cache operations
│   │   │   │                                # - Key generation
│   │   │   └─ Cache management functions
│   │   │
│   │   └── security.py                      # Authentication & Security
│   │       │                                # - Password hashing (bcrypt)
│   │       │                                # - JWT token creation/decoding
│   │       │                                # - Token data model
│   │       └─ Key functions:
│   │           - hash_password()
│   │           - verify_password()
│   │           - create_access_token()
│   │           - create_refresh_token()
│   │           - decode_token()
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   └── models.py                        # SQLAlchemy ORM Models
│   │       │
│   │       ├── User Management:
│   │       │   ├── Role              - User roles (admin, therapist, front_office)
│   │       │   ├── User              - System users with authentication
│   │       │   └── UserRole          - Many-to-many user-role relationship
│   │       │
│   │       ├── Organization:
│   │       │   └── Region            - Geographic regions for data isolation
│   │       │
│   │       ├── Patients & Therapy:
│   │       │   ├── Patient           - Patient demographics & medical history
│   │       │   ├── Therapist         - Therapist profiles (linked to User)
│   │       │   ├── Appointment       - Scheduled appointments
│   │       │   ├── Session           - Completed therapy sessions
│   │       │   ├── SessionNote       - Progress notes on sessions
│   │       │   └── SessionAssignment - Links sessions to packages
│   │       │
│   │       ├── Packages & Billing:
│   │       │   ├── Package           - Therapy packages (sessions + price)
│   │       │   ├── PatientPackage    - Patient enrollment in packages
│   │       │   ├── Invoice           - Billing documents
│   │       │   ├── InvoiceItem       - Line items on invoices
│   │       │   └── Payment           - Payment records
│   │       │
│   │       ├── Administrative:
│   │       │   ├── Notification      - User notifications
│   │       │   ├── Alert             - System alerts
│   │       │   ├── Document          - Patient documents
│   │       │   ├── AuditLog          - Complete audit trail
│   │       │   └── PatientDuplicate  - Duplicate detection records
│   │       │
│   │       └── All models include:
│   │           - id (primary key)
│   │           - created_at (timestamp)
│   │           - updated_at (timestamp)
│   │           - deleted_at (soft delete)
│   │           - region_id (region filtering where applicable)
│   │           - proper relationships & indexes
│   │
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── schemas.py                       # Pydantic Validation Schemas
│   │       │                                # Corresponds to each model
│   │       │
│   │       ├── Request Schemas:
│   │       │   └── *Create & *Update classes
│   │       │
│   │       ├── Response Schemas:
│   │       │   └── *Response classes
│   │       │
│   │       └── Utility Schemas:
│   │           ├── PaginationParams
│   │           └── PaginatedResponse
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes/
│   │       ├── __init__.py                  # Router aggregator
│   │       ├── auth.py                      # Authentication endpoints
│   │       │   ├── POST /auth/register      - Register new user
│   │       │   ├── POST /auth/login         - Login & get tokens
│   │       │   └── POST /auth/refresh       - Refresh access token
│   │       │
│   │       ├── patients.py                  # Patient management
│   │       │   ├── GET /patients            - List patients (region-filtered)
│   │       │   ├── POST /patients           - Create patient
│   │       │   ├── GET /patients/{id}       - Get patient details
│   │       │   ├── PATCH /patients/{id}     - Update patient
│   │       │   ├── DELETE /patients/{id}    - Delete patient (soft delete)
│   │       │   └── GET /patients/{id}/packages - List patient packages
│   │       │
│   │       ├── appointments.py              # Appointment scheduling
│   │       │   ├── GET /appointments        - List with date filtering
│   │       │   ├── POST /appointments       - Create appointment
│   │       │   ├── GET /appointments/{id}   - Get appointment
│   │       │   ├── PATCH /appointments/{id} - Update appointment
│   │       │   └── DELETE /appointments/{id} - Delete appointment
│   │       │
│   │       ├── sessions.py                  # Session management
│   │       │   ├── GET /sessions            - List sessions
│   │       │   ├── POST /sessions           - Create session
│   │       │   ├── GET /sessions/{id}       - Get session
│   │       │   ├── PATCH /sessions/{id}     - Update session
│   │       │   ├── POST /sessions/{id}/notes - Add session note
│   │       │   └── POST /sessions/{id}/complete - Mark as completed
│   │       │
│   │       ├── therapists.py                # Therapist management
│   │       │   ├── GET /therapists          - List therapists
│   │       │   └── GET /therapists/{id}     - Get therapist
│   │       │
│   │       └── billing.py                   # Billing & payments
│   │           ├── POST /billing/invoices   - Create invoice
│   │           ├── GET /billing/invoices    - List invoices
│   │           ├── GET /billing/invoices/{id} - Get invoice
│   │           ├── PATCH /billing/invoices/{id} - Update invoice
│   │           ├── POST /billing/invoices/{id}/issue - Issue invoice
│   │           ├── POST /billing/payments   - Record payment
│   │           ├── GET /billing/payments    - List payments
│   │           └── PATCH /billing/payments/{id} - Update payment status
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── user_service.py                  # User business logic
│   │   │   ├── UserService                  - User CRUD operations
│   │   │   │   ├── create_user()
│   │   │   │   ├── get_user_by_username()
│   │   │   │   ├── get_user_by_email()
│   │   │   │   ├── authenticate_user()
│   │   │   │   ├── get_user_roles()
│   │   │   │   ├── assign_role()
│   │   │   │   ├── update_user()
│   │   │   │   ├── delete_user()
│   │   │   │   └── list_users()
│   │   │   │
│   │   │   └── AuthService                  - Authentication business logic
│   │   │       ├── create_tokens()          - Generate JWT tokens
│   │   │       └── update_last_login()
│   │   │
│   │   ├── patient_service.py               # Patient operations
│   │   │   └── PatientService
│   │   │       ├── create_patient()         - Create with duplicate detection
│   │   │       ├── get_patient_by_id()      - With region filtering
│   │   │       ├── update_patient()
│   │   │       ├── delete_patient()
│   │   │       ├── list_patients()          - With region filtering
│   │   │       ├── get_patient_packages()
│   │   │       ├── check_package_availability()
│   │   │       ├── update_package_sessions()
│   │   │       └── get_duplicate_patients()
│   │   │
│   │   ├── appointment_service.py           # Appointment & session logic
│   │   │   ├── AppointmentService
│   │   │   │   ├── create_appointment()     - Validates therapist & package
│   │   │   │   ├── get_appointment_by_id()  - With region filtering
│   │   │   │   ├── update_appointment()
│   │   │   │   ├── delete_appointment()
│   │   │   │   ├── list_appointments()      - Complex filtering
│   │   │   │   └── check_therapist_availability()
│   │   │   │
│   │   │   └── SessionService
│   │   │       ├── create_session()
│   │   │       ├── get_session_by_id()
│   │   │       ├── update_session()
│   │   │       ├── delete_session()
│   │   │       ├── list_sessions()
│   │   │       ├── add_session_note()
│   │   │       └── complete_session()       - Auto-updates package
│   │   │
│   │   └── billing_service.py               # Billing operations
│   │       ├── BillingService               - Invoice management
│   │       │   ├── create_invoice()
│   │       │   ├── get_invoice_by_id()
│   │       │   ├── update_invoice()
│   │       │   ├── delete_invoice()
│   │       │   ├── list_invoices()
│   │       │   └── mark_as_issued()
│   │       │
│   │       ├── PaymentService               - Payment management
│   │       │   ├── record_payment()
│   │       │   ├── get_payment_by_id()
│   │       │   ├── update_payment_status()
│   │       │   └── list_payments()
│   │       │
│   │       └── TherapistService             - Therapist management
│   │           ├── get_therapist_by_id()
│   │           └── list_therapists()
│   │
│   ├── dependencies/
│   │   ├── __init__.py
│   │   └── auth.py                          # Dependency injection for auth
│   │       ├── get_current_user()           - JWT authentication
│   │       ├── get_current_admin()          - Admin role check
│   │       ├── get_user_roles()             - Get user's roles
│   │       ├── check_region_access()        - Region access validation
│   │       └── get_token_data()             - Extract token data
│   │
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── middleware.py                    # Custom middleware
│   │       ├── RegionAccessMiddleware       - Region-based filtering
│   │       ├── LoggingMiddleware            - JSON structured logging
│   │       └── QueryFilteringMiddleware     - Adds region to queries
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py                        # Logging configuration
│       │   ├── JSONFormatter                - JSON structured logs
│       │   └── setup_logging()              - Logger setup
│       │
│       └── query_utils.py                   # Query & utility functions
│           ├── filter_by_region()           - Apply region filtering
│           ├── calculate_similarity()       - String similarity
│           ├── detect_duplicate_patients()  - Duplicate detection
│           ├── check_exact_duplicates()     - Exact match detection
│           ├── format_phone()               - Phone normalization
│           └── soft_delete()                - Soft delete operation
│
├── gunicorn_conf.py                         # Gunicorn configuration
│   │                                        # - 10 workers
│   │                                        # - Uvicorn worker class
│   │                                        # - Connection pooling
│   │                                        # - Logging setup
│   │                                        # - Process management
│   └─ Optimized for production
│
├── init_db.py                               # Database initialization script
│   │                                        # - Creates tables
│   │                                        # - Creates default roles
│   │                                        # - Creates default regions
│   │                                        # - Creates sample users
│   │                                        # - Creates sample patients
│   └─ Run once after setting up database
│
├── requirements.txt                         # Python dependencies
│   │
│   ├── FastAPI & Uvicorn
│   ├── SQLAlchemy & ORM
│   ├── MySQL driver
│   ├── Redis client
│   ├── JWT & Security
│   ├── Pydantic validation
│   ├── Gunicorn
│   └── Utility libraries
│
├── .env.example                             # Environment variables template
│   │                                        # Copy and configure for your setup
│   └─ Do NOT commit .env file
│
├── README.md                                # Comprehensive documentation
│   │                                        # - Tech stack
│   │                                        # - Features overview
│   │                                        # - Database design
│   │                                        # - API endpoints
│   │                                        # - Region-based access control
│   │                                        # - Setup instructions
│   │                                        # - Example workflows
│   │                                        # - Security features
│   └─ IMPORTANT: Read first!
│
├── QUICKSTART.md                            # Quick setup guide
│   │                                        # - 5-minute setup
│   │                                        # - Basic testing
│   │                                        # - Troubleshooting
│   └─ Start here for development
│
├── EXAMPLES.md                              # Comprehensive API examples
│   │                                        # - Authentication examples
│   │                                        # - CRUD operations
│   │                                        # - Complex workflows
│   │                                        # - Error handling
│   │                                        # - Advanced queries
│   └─ Copy-paste examples
│
├── DEPLOYMENT.md                            # Production deployment guide
│   │                                        # - Pre-deployment checklist
│   │                                        # - Environment setup
│   │                                        # - Gunicorn + Nginx
│   │                                        # - Docker deployment
│   │                                        # - Database migration
│   │                                        # - Security hardening
│   │                                        # - Monitoring & logging
│   │                                        # - Backup & recovery
│   └─ Use for production
│
└── .gitignore (recommended)
    ├── __pycache__/
    ├── *.pyc
    ├── .env
    ├── venv/
    ├── .DS_Store
    └── logs/
```

## Key Files Summary

### Core Application Files

| File | Purpose | Size |
|------|---------|------|
| `app/main.py` | FastAPI entry point | ~400 lines |
| `app/models/models.py` | All 18 database models | ~600 lines |
| `app/schemas/schemas.py` | All Pydantic schemas | ~400 lines |
| `gunicorn_conf.py` | Gunicorn with 10 workers | ~50 lines |

### Service Layer

| File | # of Services | Functions |
|------|---------------|-----------|
| `user_service.py` | 2 | 12 functions |
| `patient_service.py` | 1 | 10 functions |
| `appointment_service.py` | 2 | 11 functions |
| `billing_service.py` | 3 | 13 functions |

### API Endpoints

| Router | Endpoints | Resources |
|--------|-----------|-----------|
| `auth.py` | 3 | Register, Login, Refresh |
| `patients.py` | 6 | Patient CRUD + Packages |
| `appointments.py` | 5 | Appointment CRUD + List |
| `sessions.py` | 6 | Session CRUD + Notes + Complete |
| `therapists.py` | 2 | Get + List therapists |
| `billing.py` | 8 | Invoices + Payments |

**Total: 30 API endpoints**

## Features At a Glance

✅ **Complete Authentication**
- JWT-based with access & refresh tokens
- Password hashing with bcrypt
- Role-based access control (Admin, Therapist, Front Office)

✅ **Region-Based Access Control**
- Enforced at middleware level
- Enforced at query level
- Automatic filtering for non-admin users

✅ **Patient Management**
- Full CRUD operations
- Automatic duplicate detection
- Package tracking
- Medical history

✅ **Appointment Scheduling**
- Therapist availability checking
- Package session validation
- Conflict detection
- Date range filtering

✅ **Session Management**
- Auto-incrementing session numbers
- Progress note tracking
- Automatic billing updates
- Package session consumption

✅ **Billing System**
- Invoice generation
- Line item management
- Payment tracking
- Status management (draft → issued → paid)

✅ **Redis Integration**
- Cache management
- Background job support
- Session storage

✅ **Comprehensive Logging**
- JSON-structured logging
- Request/response tracking
- Audit trail
- Error logging

✅ **Production Ready**
- Gunicorn with 10 workers
- Connection pooling
- Soft deletes
- Proper error handling
- Health checks

## Code Quality Features

- **Modularity**: Clear separation of concerns (models, schemas, services, routes)
- **Reusability**: Shared utilities for query filtering, logging, caching
- **Type Safety**: Pydantic validation on all inputs
- **Error Handling**: Proper HTTP status codes and error messages
- **Logging**: Structured JSON logging for debugging
- **Security**: Password hashing, JWT, role-based access
- **Documentation**: Comprehensive docstrings and comments

## Database Features

- **18 Database Tables** covering all business requirements
- **Soft Deletes** for audit trail preservation
- **Strategic Indexes** for performance
- **Relationships** with proper foreign keys
- **Region Isolation** built into schema
- **Audit Logging** for compliance

---

**For more information, see the respective documentation files.**
