# Database Documentation

This document explains every table and column used by the Autism Therapy Management System backend.

The database is designed around these main areas:

- User access: who can log in, their role, and their region.
- Patient care: patient details, appointments, sessions, notes, packages, documents, and progress.
- Therapist operations: therapist profiles, assigned sessions, utilization, and availability.
- Billing: invoices, invoice items, payments, unpaid sessions, and revenue analytics.
- Safety and operations: alerts, notifications, audit logs, duplicate detection, and waitlist.

Most operational tables include:

- `created_at`: when the row was created.
- `updated_at`: when the row was last changed.
- `deleted_at`: soft-delete marker. If this is not null, the row should be treated as deleted while preserving history.

## Enum Values

### AppointmentStatusEnum

Used by `appointments.status`.

| Value | Meaning |
| --- | --- |
| `scheduled` | Appointment is booked but not yet confirmed or started. |
| `confirmed` | Patient/clinic has confirmed attendance. |
| `in_progress` | Appointment is currently happening. |
| `completed` | Appointment was completed. |
| `cancelled` | Appointment was cancelled. |
| `no_show` | Patient did not attend. |

### SessionStatusEnum

Used by `sessions.status`.

| Value | Meaning |
| --- | --- |
| `scheduled` | Therapy session is planned for a future or current date. |
| `in_progress` | Therapy session is currently being conducted. |
| `completed` | Therapy session has been completed. |
| `cancelled` | Therapy session was cancelled. |
| `no_show` | Patient did not attend the session. |

### InvoiceStatusEnum

Used by `invoices.status`.

| Value | Meaning |
| --- | --- |
| `draft` | Invoice is prepared but not sent/issued. |
| `issued` | Invoice has been issued to the patient/guardian. |
| `overdue` | Invoice due date has passed and balance remains. |
| `paid` | Invoice is fully paid. |
| `cancelled` | Invoice was cancelled. |

### PaymentStatusEnum

Used by `payments.status`.

| Value | Meaning |
| --- | --- |
| `pending` | Payment is initiated but not confirmed. |
| `completed` | Payment is successful and counted in revenue. |
| `failed` | Payment attempt failed. |
| `refunded` | Payment was returned. |

### DocumentTypeEnum

Used by `documents.document_type`.

| Value | Meaning |
| --- | --- |
| `assessment` | Assessment document. |
| `progress_report` | Periodic progress report. |
| `consent_form` | Consent or permission document. |
| `evaluation` | Clinical/therapy evaluation document. |
| `other` | Any other patient document. |

## Table: `roles`

Stores system roles such as admin, therapist, and front office.

Why we use it: role data controls what a logged-in user can access. For example, therapists should only see their assigned sessions, while admins can see analytics and billing.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique role id used by `user_roles`. |
| `name` | String(50), unique | Yes | Machine-readable role name such as `admin`, `therapist`, or `front_office`. |
| `description` | Text | No | Human-readable explanation of the role. |
| `created_at` | DateTime | Yes | Tracks when the role was created. |
| `updated_at` | DateTime | Yes | Tracks when role metadata changed. |
| `deleted_at` | DateTime | No | Allows soft deletion without breaking old user-role history. |

Relationships:

- One role can be assigned to many users through `user_roles`.

## Table: `users`

Stores login users and staff profile identity.

Why we use it: every staff member who logs into the system has a user row. This supports authentication, role assignment, region-level access, and therapist profile linking.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique user id used across auth, notes, notifications, audit logs, and therapist profiles. |
| `username` | String(100), unique | Yes | Login/display identifier. |
| `email` | String(255), unique | Yes | Login email and contact identity. |
| `hashed_password` | String(255) | Yes | Secure password hash. Raw passwords are never stored. |
| `first_name` | String(100) | Yes | Staff first name for display. |
| `last_name` | String(100) | Yes | Staff last name for display. |
| `is_active` | Boolean | Yes | Disables login/access without deleting history. |
| `is_verified` | Boolean | Yes | Marks account verification state. |
| `region_id` | Foreign key to `regions.id` | Yes | Restricts data access to the staff member's clinic/region. |
| `phone` | String(20) | No | Staff contact number. |
| `last_login` | DateTime | No | Tracks recent login activity. |
| `created_at` | DateTime | Yes | Tracks when staff account was created. |
| `updated_at` | DateTime | Yes | Tracks when staff account was updated. |
| `deleted_at` | DateTime | No | Soft delete for staff accounts. |

Relationships:

- Belongs to one `regions` row.
- Can have many `user_roles`.
- Can have one `therapists` profile.

## Table: `user_roles`

Links users to roles.

Why we use it: this lets one user have one or more permissions without storing role text directly inside the `users` table.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique assignment id. |
| `user_id` | Foreign key to `users.id` | Yes | Identifies which user receives the role. |
| `role_id` | Foreign key to `roles.id` | Yes | Identifies the assigned role. |
| `created_at` | DateTime | Yes | Tracks when the role was assigned. |
| `updated_at` | DateTime | Yes | Tracks changes to the assignment. |
| `deleted_at` | DateTime | No | Soft-removes a role assignment. |

Constraints and indexes:

- Unique constraint on `user_id` + `role_id` prevents duplicate role assignments.

## Table: `regions`

Stores clinic branches, regions, or operational areas.

Why we use it: users, patients, therapists, appointments, and invoices are region-scoped so each branch can manage its own data.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique region id. |
| `name` | String(100), unique | Yes | Region display name. |
| `code` | String(10), unique | Yes | Short region code for references and reports. |
| `location` | String(255) | No | Physical or descriptive location. |
| `created_at` | DateTime | Yes | Tracks when region was added. |
| `updated_at` | DateTime | Yes | Tracks region changes. |
| `deleted_at` | DateTime | No | Soft delete for inactive regions. |

Relationships:

- Has many users, patients, therapists, appointments, and invoices.

## Table: `patients`

Stores patient demographic, contact, diagnosis, and clinical background.

Why we use it: this is the central patient profile used by scheduling, sessions, packages, billing, alerts, documents, and duplicate detection.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique patient id. |
| `first_name` | String(100) | Yes | Patient first name. |
| `last_name` | String(100) | Yes | Patient last name. |
| `date_of_birth` | Date | Yes | Used for age, care planning, and duplicate checks. |
| `gender` | String(20) | No | Optional demographic data. |
| `email` | String(255) | No | Patient/guardian email contact. |
| `phone` | String(20) | No | Patient/guardian phone contact. |
| `address` | Text | No | Patient address. |
| `diagnosis` | String(255) | No | Main diagnosis used in dashboards and clinical context. |
| `medical_history` | Text | No | Background notes important for therapy planning. |
| `emergency_contact` | String(255) | No | Emergency contact details. |
| `region_id` | Foreign key to `regions.id` | Yes | Assigns patient to clinic/region. |
| `created_at` | DateTime | Yes | Tracks patient profile creation. |
| `updated_at` | DateTime | Yes | Tracks patient profile updates. |
| `deleted_at` | DateTime | No | Soft delete while preserving historical sessions/billing. |

Relationships:

- Belongs to one region.
- Has many appointments, sessions, packages, invoices, documents, and alerts.

## Table: `therapists`

Stores therapist-specific professional profile data.

Why we use it: a therapist is a user with clinical metadata, availability, and assigned appointments/sessions.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique therapist id used by appointments and sessions. |
| `user_id` | Foreign key to `users.id`, unique | Yes | Links therapist profile to login user. |
| `license_number` | String(100), unique | Yes | Professional license identifier. |
| `specialization` | String(255) | No | Shows therapy specialty such as speech, behaviour, ASD, OT. |
| `qualification` | Text | No | Stores degrees/certifications/bio. |
| `is_available` | Boolean | Yes | Controls whether therapist can be scheduled. |
| `region_id` | Foreign key to `regions.id` | Yes | Assigns therapist to a clinic/region. |
| `created_at` | DateTime | Yes | Tracks therapist profile creation. |
| `updated_at` | DateTime | Yes | Tracks therapist profile updates. |
| `deleted_at` | DateTime | No | Soft delete while preserving historical sessions. |

Relationships:

- Belongs to one user and one region.
- Has many appointments and sessions.

## Table: `appointments`

Stores scheduled calendar appointments.

Why we use it: appointments represent calendar slots. They are used by the calendar view, therapist schedules, no-show tracking, and conversion into therapy sessions.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique appointment id. |
| `patient_id` | Foreign key to `patients.id` | Yes | Patient booked for the appointment. |
| `therapist_id` | Foreign key to `therapists.id` | Yes | Therapist assigned to the appointment slot. |
| `start_time` | DateTime | Yes | Appointment start date/time for calendar placement. |
| `end_time` | DateTime | Yes | Appointment end date/time and duration calculation. |
| `status` | Enum | Yes | Tracks scheduled, confirmed, completed, cancelled, no-show, etc. |
| `notes` | Text | No | Appointment-specific notes. |
| `region_id` | Foreign key to `regions.id` | Yes | Region-level access and reporting. |
| `created_at` | DateTime | Yes | Tracks when appointment was booked. |
| `updated_at` | DateTime | Yes | Tracks schedule/status changes. |
| `deleted_at` | DateTime | No | Soft delete/cancel cleanup without losing history. |

Relationships:

- Belongs to patient, therapist, and region.
- Can have related sessions.

## Table: `sessions`

Stores actual therapy sessions assigned to therapists.

Why we use it: this is the core clinical work record. Therapist login is scoped to this table, and analytics use it to calculate completed sessions, future sessions, patient progress, utilization, and unbilled revenue leakage.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique session id. |
| `patient_id` | Foreign key to `patients.id` | Yes | Patient receiving therapy. |
| `therapist_id` | Foreign key to `therapists.id` | Yes | Therapist assigned to the session. |
| `appointment_id` | Foreign key to `appointments.id` | No | Links session to the calendar appointment, if created from one. |
| `session_number` | Integer | Yes | Session sequence number inside a plan/package. |
| `duration_minutes` | Integer | Yes | Used for workload, utilization, and billing context. |
| `status` | Enum | Yes | Tracks scheduled/current/completed/cancelled/no-show session state. |
| `session_date` | Date | Yes | Used for past, present, future session filtering and trends. |
| `progress_notes` | Text | No | Clinical progress summary. |
| `billing_status` | String(50) | Yes | Tracks whether session is pending, billed, paid, or waived. |
| `created_at` | DateTime | Yes | Tracks when session was created. |
| `updated_at` | DateTime | Yes | Tracks session changes. |
| `deleted_at` | DateTime | No | Soft delete while keeping audit/reporting context. |

Relationships:

- Belongs to patient, therapist, and optionally appointment.
- Has session notes.
- Has package assignments through `session_assignments`.

## Table: `session_notes`

Stores detailed notes written for a session.

Why we use it: therapists need to record observations, assessment notes, plans, handover notes, and progress updates without overwriting the main session row.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique note id. |
| `session_id` | Foreign key to `sessions.id` | Yes | Connects the note to a session. |
| `note_type` | String(50) | Yes | Categorizes the note, such as progress, assessment, plan, observation, handover. |
| `content` | Text | Yes | Actual note text. |
| `created_by` | Foreign key to `users.id` | Yes | Identifies who wrote the note. |
| `created_at` | DateTime | Yes | Tracks when note was written. |
| `updated_at` | DateTime | Yes | Tracks note edits. |
| `deleted_at` | DateTime | No | Soft delete for clinical note history control. |

## Table: `session_assignments`

Links a session to the package it consumes.

Why we use it: it tells the system which paid package/session bundle a therapy session belongs to. This supports package progress and billing calculations.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique assignment id. |
| `session_id` | Foreign key to `sessions.id` | Yes | Session being assigned. |
| `package_id` | Foreign key to `packages.id` | Yes | Package that covers the session. |
| `created_at` | DateTime | Yes | Tracks assignment creation. |
| `updated_at` | DateTime | Yes | Tracks assignment changes. |
| `deleted_at` | DateTime | No | Soft delete if assignment is reversed. |

## Table: `packages`

Stores therapy package templates.

Why we use it: packages define how many sessions a patient buys, price, and duration. They are used for billing, remaining sessions, and progress tracking.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique package id. |
| `name` | String(100) | Yes | Package display name. |
| `description` | Text | No | Explains what the package includes. |
| `total_sessions` | Integer | Yes | Number of sessions included. |
| `price` | Float | Yes | Total package price. |
| `duration_days` | Integer | No | Validity duration in days. |
| `is_active` | Boolean | Yes | Controls whether package can be sold/assigned. |
| `created_at` | DateTime | Yes | Tracks package creation. |
| `updated_at` | DateTime | Yes | Tracks package updates. |
| `deleted_at` | DateTime | No | Soft delete for retired packages. |

## Table: `patient_packages`

Stores packages purchased/assigned to patients.

Why we use it: this is the patient's actual active therapy plan. It calculates total slots, completed slots, remaining slots, and package progress.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique patient-package id. |
| `patient_id` | Foreign key to `patients.id` | Yes | Patient who owns the package. |
| `package_id` | Foreign key to `packages.id` | Yes | Package template purchased/assigned. |
| `start_date` | Date | Yes | Package start date. |
| `end_date` | Date | No | Package expiry/end date. |
| `sessions_completed` | Integer | Yes | Count of completed sessions under the package. |
| `sessions_remaining` | Integer | Yes | Count of available sessions left. |
| `status` | String(50) | Yes | Active, completed, expired, cancelled, etc. |
| `created_at` | DateTime | Yes | Tracks assignment creation. |
| `updated_at` | DateTime | Yes | Tracks package progress changes. |
| `deleted_at` | DateTime | No | Soft delete while preserving billing/session history. |

## Table: `invoices`

Stores invoice header and payment summary.

Why we use it: invoices drive receivables, overdue alerts, GST/tax calculations, billing dashboard, and revenue collection tracking.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique invoice id. |
| `invoice_number` | String(100), unique | Yes | Human/business invoice reference. |
| `patient_id` | Foreign key to `patients.id` | Yes | Patient/guardian being billed. |
| `region_id` | Foreign key to `regions.id` | Yes | Region issuing the invoice. |
| `issue_date` | Date | Yes | Date invoice was created/issued. |
| `due_date` | Date | Yes | Used to detect overdue invoices. |
| `total_amount` | Float | Yes | Total invoice amount. |
| `paid_amount` | Float | Yes | Amount collected against invoice. |
| `status` | Enum | Yes | Draft, issued, overdue, paid, cancelled. |
| `description` | Text | No | Summary of billed service/package. |
| `created_at` | DateTime | Yes | Tracks invoice creation. |
| `updated_at` | DateTime | Yes | Tracks invoice changes/payments. |
| `deleted_at` | DateTime | No | Soft delete/cancel support. |

Relationships:

- Has many invoice items.
- Has many payments.

## Table: `invoice_items`

Stores line items inside an invoice.

Why we use it: invoices can contain multiple services, sessions, packages, or adjustments. Line items explain exactly what the invoice amount is for.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique invoice item id. |
| `invoice_id` | Foreign key to `invoices.id` | Yes | Parent invoice. |
| `description` | String(255) | Yes | What is being billed. |
| `quantity` | Integer | Yes | Number of units/sessions/packages. |
| `unit_price` | Float | Yes | Price per unit. |
| `total_price` | Float | Yes | Quantity multiplied by unit price. |
| `created_at` | DateTime | Yes | Tracks item creation. |
| `updated_at` | DateTime | Yes | Tracks item changes. |
| `deleted_at` | DateTime | No | Soft delete for invoice correction history. |

## Table: `payments`

Stores payments collected against invoices.

Why we use it: revenue dashboards should be based on completed payments, not static values. Payments also support partial payment tracking and invoice balance calculation.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique payment id. |
| `invoice_id` | Foreign key to `invoices.id` | Yes | Invoice being paid. |
| `amount` | Float | Yes | Payment amount received. |
| `payment_method` | String(50) | Yes | Cash, card, online, bank transfer, etc. |
| `transaction_id` | String(100) | No | Payment gateway/bank reference. |
| `status` | Enum | Yes | Pending, completed, failed, refunded. |
| `payment_date` | DateTime | No | Actual payment date used for revenue charts. |
| `notes` | Text | No | Payment remarks. |
| `created_at` | DateTime | Yes | Tracks payment record creation. |
| `updated_at` | DateTime | Yes | Tracks status/notes changes. |
| `deleted_at` | DateTime | No | Soft delete/correction support. |

## Table: `notifications`

Stores user notifications.

Why we use it: users need in-app alerts/reminders for billing, appointments, operational issues, or system messages.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique notification id. |
| `user_id` | Foreign key to `users.id` | Yes | Recipient user. |
| `title` | String(255) | Yes | Short notification title. |
| `message` | Text | Yes | Full notification text. |
| `notification_type` | String(50) | Yes | Category such as alert, reminder, billing, appointment. |
| `is_read` | Boolean | Yes | Tracks whether user has read it. |
| `data` | JSON | No | Extra metadata for routing/action buttons. |
| `created_at` | DateTime | Yes | Tracks notification creation. |
| `updated_at` | DateTime | Yes | Tracks read/status changes. |
| `deleted_at` | DateTime | No | Soft delete for notification cleanup. |

## Table: `alerts`

Stores operational and clinical/business alerts.

Why we use it: alerts power the alert dashboard. They flag unbilled completed sessions, overdue payments, low utilization, duplicate patients, package expiry, reassignment, and custom issues.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique alert id. |
| `patient_id` | Foreign key to `patients.id` | No | Patient related to the alert, if applicable. |
| `alert_type` | String(100) | Yes | Machine-readable alert category. |
| `title` | String(255) | Yes | Short alert title. |
| `description` | Text | Yes | Full alert explanation. |
| `severity` | String(20) | Yes | Risk level such as low, medium, high, critical. |
| `is_active` | Boolean | Yes | Open/resolved state. Active means unresolved. |
| `metadata` | JSON | No | Extra entity info such as linked session/invoice id. |
| `created_at` | DateTime | Yes | Tracks alert creation. |
| `updated_at` | DateTime | Yes | Tracks alert changes/resolution. |
| `deleted_at` | DateTime | No | Soft delete for alert cleanup. |

## Table: `waitlist_entries`

Stores patients waiting for a preferred slot or service.

Why we use it: the calendar sidebar needs real waitlist data so staff can fill open slots based on service, priority, preferred therapist, and preferred time.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique waitlist entry id. |
| `patient_id` | Foreign key to `patients.id` | Yes | Patient waiting for a slot. |
| `requested_service` | String(255) | Yes | Therapy/service requested. |
| `preferred_therapist_id` | Foreign key to `therapists.id` | No | Preferred therapist if patient/guardian has one. |
| `priority` | String(20) | Yes | Priority such as high, medium, low. |
| `preferred_days` | JSON | No | Preferred days of week, stored flexibly. |
| `preferred_time` | String(100) | No | Preferred time window such as morning/evening. |
| `notes` | Text | No | Extra waitlist notes. |
| `status` | String(50) | Yes | Waiting, scheduled, cancelled, etc. |
| `requested_at` | DateTime | Yes | Used to calculate how long patient has been waiting. |
| `created_at` | DateTime | Yes | Tracks row creation. |
| `updated_at` | DateTime | Yes | Tracks waitlist changes. |
| `deleted_at` | DateTime | No | Soft delete for removed waitlist rows. |

## Table: `documents`

Stores patient document metadata.

Why we use it: the system needs to track assessment files, consent forms, reports, evaluations, and other patient documents without storing the actual file blob in the main patient table.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique document id. |
| `patient_id` | Foreign key to `patients.id` | Yes | Patient the document belongs to. |
| `document_type` | Enum | Yes | Document category. |
| `title` | String(255) | Yes | Display name. |
| `file_path` | String(500) | Yes | Path/URL where file is stored. |
| `file_size` | Integer | Yes | File size for display/validation. |
| `mime_type` | String(100) | Yes | File type such as PDF/image. |
| `uploaded_by` | Foreign key to `users.id` | Yes | User who uploaded the file. |
| `description` | Text | No | Notes about the document. |
| `is_confidential` | Boolean | Yes | Flags sensitive documents for tighter handling. |
| `created_at` | DateTime | Yes | Tracks upload time. |
| `updated_at` | DateTime | Yes | Tracks metadata changes. |
| `deleted_at` | DateTime | No | Soft delete for removed documents. |

## Table: `audit_logs`

Stores system audit history.

Why we use it: healthcare and billing systems need traceability. Audit logs show who changed what, when, and what values changed.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique audit event id. |
| `user_id` | Foreign key to `users.id` | No | User who performed the action, if known. |
| `entity_type` | String(100) | Yes | Table/entity changed, such as patients or sessions. |
| `entity_id` | Integer | Yes | Id of the changed record. |
| `action` | String(50) | Yes | Action such as CREATE, UPDATE, DELETE, VIEW. |
| `old_values` | JSON | No | Previous values before change. |
| `new_values` | JSON | No | New values after change. |
| `ip_address` | String(45) | No | Request IP address for traceability. |
| `user_agent` | String(500) | No | Browser/client info. |
| `created_at` | DateTime | Yes | When the audit event occurred. |

## Table: `patient_duplicates`

Stores possible duplicate patient matches.

Why we use it: duplicate patient profiles can split medical history, billing, and session progress. This table stores potential matches for review/merge workflows.

| Column | Type | Required | Why we use it |
| --- | --- | --- | --- |
| `id` | Integer, primary key | Yes | Unique duplicate detection id. |
| `patient_id_1` | Foreign key to `patients.id` | Yes | First patient in possible duplicate pair. |
| `patient_id_2` | Foreign key to `patients.id` | Yes | Second patient in possible duplicate pair. |
| `similarity_score` | Float | Yes | Match confidence score from 0 to 1. |
| `matched_fields` | JSON | Yes | Fields that matched, such as phone, name, DOB. |
| `status` | String(50) | Yes | Pending, reviewed, merged, rejected. |
| `reviewed_by` | Foreign key to `users.id` | No | User who reviewed the duplicate suggestion. |
| `reviewed_at` | DateTime | No | Review timestamp. |
| `notes` | Text | No | Reviewer notes. |
| `created_at` | DateTime | Yes | When duplicate was detected. |
| `updated_at` | DateTime | Yes | Tracks review/status changes. |
| `deleted_at` | DateTime | No | Soft delete for cleanup. |

## Important Reporting Logic

### Therapist Login Scope

When a therapist logs in:

- `sessions` are filtered by `sessions.therapist_id`.
- `patients` are filtered to patients who have sessions with that therapist.
- `appointments` are filtered by therapist.
- analytics patient progress is calculated only from that therapist's assigned patients/sessions.

This is why `sessions.therapist_id`, `patients.id`, and `therapists.user_id` are critical columns.

### Patient Progress

Patient progress is calculated from:

- `patient_packages.package_id`
- `packages.total_sessions`
- `patient_packages.sessions_completed`
- `patient_packages.sessions_remaining`
- `sessions.status`
- `sessions.session_date`
- `sessions.therapist_id`

This tells the UI:

- total slots,
- completed slots,
- remaining slots,
- current/future session,
- therapist currently handling the patient.

### Therapist Utilization

Therapist utilization is calculated from:

- total sessions assigned to therapist,
- completed sessions,
- scheduled/current sessions.

Main columns:

- `sessions.therapist_id`
- `sessions.status`
- `sessions.duration_minutes`
- `sessions.session_date`

### Billing Dashboard

Billing dashboard values are calculated from:

- `invoices.total_amount`
- `invoices.paid_amount`
- `invoices.due_date`
- `invoices.status`
- `payments.amount`
- `payments.status`
- `payments.payment_date`
- completed sessions where `sessions.billing_status` is still pending.

### Alerts

Alerts are generated or displayed from:

- `alerts.alert_type`
- `alerts.severity`
- `alerts.is_active`
- `alerts.metadata`
- overdue invoices,
- unbilled completed sessions,
- expiring/low remaining packages.

### Waitlist

Waitlist sidebar uses:

- `waitlist_entries.patient_id`
- `waitlist_entries.requested_service`
- `waitlist_entries.preferred_therapist_id`
- `waitlist_entries.priority`
- `waitlist_entries.preferred_days`
- `waitlist_entries.preferred_time`
- `waitlist_entries.requested_at`

This lets staff fill empty calendar slots with the right patient based on priority and availability.

