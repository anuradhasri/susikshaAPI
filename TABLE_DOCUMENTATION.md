# Database Table Usage Documentation

This document explains every table defined in `app/models/models.py`: what the table stores, when the app uses it, and how it connects to other tables.

The product UI says **Child**, while the database still uses the older table name `patients`. In this document, **child/patient** means the same record.

## Common Patterns

| Pattern | Meaning |
|---|---|
| `id` | Primary key for the table. |
| `created_at`, `created_date` | When the row was created. |
| `updated_at`, `updated_date` | When the row was last updated. |
| `deleted_at` | Soft delete marker. If this has a value, the row should normally be hidden. |
| `created_by`, `updated_by` | User id that created or updated the row. |
| `status_id` | Points to a status master table instead of storing status text directly. |

## Login And Authentication Flow

When a user logs in from `/api/v1/ui/auth/login`:

1. The backend looks up the email in `users`.
2. It verifies the submitted password against `users.hashed_password`.
3. It reads user roles through `user_roles` and `roles`.
4. It reads branch access through `user_region_mapping`.
5. It creates access and refresh tokens with user id, email, roles, and region ids.
6. It updates `users.last_login`.
7. The frontend stores the returned token session in browser local storage.

Main tables used during login:

| Table | Used for |
|---|---|
| `users` | Find account, check active state, verify password, update last login. |
| `roles` | Master list of roles such as admin, therapist, front office. |
| `user_roles` | Maps the logged-in user to their roles. |
| `user_region_mapping` | Tells which branches/regions the user can access. |
| `password_reset_tokens` | Used only in forgot/reset password flow, not normal login. |

## Identity, Access, And Region Tables

### `users`

Stores login accounts for admins, front office staff, therapists, and other system users.

Used when:

- User logs in.
- User profile is loaded.
- Role and region access are checked.
- `last_login` is updated after successful login.

Key columns:

| Column | Meaning |
|---|---|
| `username`, `email` | Unique login identity. |
| `hashed_password` | Password hash, never the plain password. |
| `first_name`, `last_name` | Display name. |
| `is_active` | If false, the account should not be allowed to login. |
| `is_verified` | Account verification flag. |
| `phone` | Contact number. |
| `last_login` | Latest successful login time. |

Relationships:

- `users.id` -> `user_roles.user_id`
- `users.id` -> `user_region_mapping.userid`
- `users.id` -> `password_reset_tokens.user_id`

### `roles`

Master table for system roles.

Used when:

- Login token is created.
- Backend checks permissions.
- User management assigns roles.

Key columns:

| Column | Meaning |
|---|---|
| `name` | Role code/name, for example `admin`. |
| `description` | Human readable role details. |

Relationships:

- `roles.id` -> `user_roles.role_id`

### `user_roles`

Mapping table between `users` and `roles`.

Used when:

- Login token is created.
- Backend decides if user is admin, therapist, or staff.
- User role assignment is changed.

Key columns:

| Column | Meaning |
|---|---|
| `user_id` | User receiving the role. |
| `role_id` | Role assigned to the user. |

Important rule:

- Unique pair: one user should not receive the same role twice.

### `user_region_mapping`

Maps users to the branches/regions they can access.

Used when:

- Login token is created with `region_ids`.
- Region-based filtering limits data shown to a user.
- User is created and assigned to a default region.

Key columns:

| Column | Meaning |
|---|---|
| `userid` | User id. |
| `regionid` | Region id the user can access. |

### `regions`

Stores clinic branches, centers, or operating locations.

Used when:

- Creating children, therapists, invoices, and appointments.
- Filtering data branch-wise.
- Restricting user access by region.

Key columns:

| Column | Meaning |
|---|---|
| `name` | Region/branch name. |
| `code` | Short unique branch code. |
| `location` | Address or location text. |

Relationships:

- `regions.id` -> `patients.region_id`
- `regions.id` -> `therapists.region_id`
- `regions.id` -> `invoices.region_id`
- `regions.id` -> `appointments.region_id`

### `password_reset_tokens`

Stores secure tokens for forgot password and reset password.

Used when:

- User requests forgot password.
- User opens reset password link.
- Password is changed using a valid token.

Key columns:

| Column | Meaning |
|---|---|
| `user_id` | User requesting reset. |
| `token_hash` | SHA/hash of the reset token. |
| `is_active` | Whether the token can still be used. |
| `expires_at` | Token expiry time. |
| `used_at` | Filled after successful reset. |
| `ip_address`, `user_agent` | Security tracking. |

## Child/Profile And Clinical Tables

### `patients`

Stores child profile information.

Used when:

- Child is created or edited.
- Child profile page loads.
- Appointment/session/payment/document records are linked to a child.
- Duplicate detection compares new child details.

Key columns:

| Column | Meaning |
|---|---|
| `first_name`, `last_name` | Child name. |
| `date_of_birth`, `gender`, `blood_group`, `nationality` | Basic details. |
| `father_name`, `mother_name`, `phone`, `email`, `address` | Parent/contact details. |
| `emergency_phone`, `alternate_contact` | Emergency contact details. |
| `diagnosis`, `clinical_observation`, `notes` | Clinical profile notes. |
| `profile_photo_path` | Uploaded photo path. |
| `registration_at` | Registration date/time. |
| `region_id` | Branch/center. |
| `assessment_answers` | JSON answer storage for older/simple assessment flow. |

Relationships:

- `patients.id` -> `patient_session_plan.patient_id`
- `patients.id` -> `payments.patient_id`
- `patients.id` -> `documents.patient_id`
- `patients.id` -> `invoices.patient_id`
- `patients.id` -> `appointments.patient_id`
- `patients.id` -> `patient_assessment.patient_id`
- `patients.id` -> `patient_therapy.patient_id`

### `patient_duplicates`

Stores possible duplicate child records.

Used when:

- A new child is created and the system finds a similar existing child.
- Staff needs to review duplicate warnings.

Key columns:

| Column | Meaning |
|---|---|
| `patient_id_1`, `patient_id_2` | Possible duplicate pair. |
| `similarity_score` | Matching score. |
| `matched_fields` | JSON fields that matched. |
| `status` | `pending`, `reviewed`, `merged`, or `rejected`. |
| `reviewed_by`, `reviewed_at` | Review audit. |

### `documents`

Stores uploaded child documents.

Used when:

- Document is uploaded from child profile.
- Uploaded files are listed or opened.

Key columns:

| Column | Meaning |
|---|---|
| `patient_id` | Child owner. |
| `document_type_id` | Type from `document_type_master`. |
| `title` | Document title. |
| `file_path` | Stored file path. |
| `file_size` | File size. |
| `uploaded_by` | User who uploaded it. |
| `description` | Optional description. |

### `document_type_master`

Master list for document categories.

Used when:

- Showing document type dropdown.
- Categorizing uploaded child files.

Key columns:

| Column | Meaning |
|---|---|
| `name` | Document type name. |
| `description` | Details about the type. |
| `status` | Active/inactive flag. |

## Therapist, Therapy, Leave, And Slot Tables

### `therapists`

Stores therapist profile records.

Used when:

- Therapist list loads.
- Appointment calendar groups slots by therapist.
- Therapist is mapped to therapies.

Key columns:

| Column | Meaning |
|---|---|
| `name` | Therapist name. |
| `qualification` | Qualification/specialization text. |
| `region_id` | Branch/center. |
| `is_active` | Whether therapist is active. |

Relationships:

- `therapists.id` -> `therapist_therapy_mapping.therapist_id`
- `therapists.id` -> `therapist_slot_mapping.therapist_id`
- `therapists.id` -> `therapist_leaves.therapist_id`

### `therapy_master`

Master table for therapy/service types.

Used when:

- Creating session plans.
- Booking slots therapy-wise.
- Mapping therapists to therapies.

Key columns:

| Column | Meaning |
|---|---|
| `name` | Therapy name, for example Speech Therapy. |
| `description` | Therapy description. |
| `is_active` | Whether therapy can be selected. |

Relationships:

- `therapy_master.id` -> `patient_session_plan_item.therapy_id`
- `therapy_master.id` -> `therapist_therapy_mapping.therapy_id`
- `therapy_master.id` -> `therapist_slot_mapping.therapy_id`

### `therapist_therapy_mapping`

Maps therapists to the therapies they can provide.

Used when:

- Booking screen filters therapists after therapy is selected.
- Admin configures therapist skills.

Key columns:

| Column | Meaning |
|---|---|
| `therapist_id` | Therapist. |
| `therapy_id` | Therapy type. |
| `is_active` | Whether this mapping is active. |

### `therapist_availability`

Stores therapist day-wise availability.

Used when:

- Building therapist schedule.
- Marking available/unavailable working hours.

Key columns:

| Column | Meaning |
|---|---|
| `therapist_id` | Therapist. |
| `availability_date` | Date. |
| `start_time`, `end_time` | Working time. |
| `break_start`, `break_end` | Break time. |
| `status` | Availability status. |
| `notes` | Extra notes. |

### `slot_master`

Master table for standard time slots.

Used when:

- Creating therapist slot mappings.
- Calendar displays slot time labels.

Key columns:

| Column | Meaning |
|---|---|
| `slot_name` | Slot label. |
| `start_time`, `end_time` | Slot timing. |
| `duration_minutes` | Slot length. |
| `is_active` | Whether slot can be used. |

### `therapist_slot_mapping`

Stores actual therapist slots for a date, slot, and therapy.

Used when:

- Calendar loads day/week/month availability.
- A slot is booked for a child.
- Slot status changes from available/booked/completed/no-show/cancelled depending on status master.

Key columns:

| Column | Meaning |
|---|---|
| `therapist_id` | Therapist owning the slot. |
| `slot_id` | Slot time from `slot_master`. |
| `slot_date` | Calendar date. |
| `therapy_id` | Therapy type for the slot. |
| `status_id` | Status from `therapist_slot_mapping_status_master`. |

Relationships:

- `therapist_slot_mapping.id` -> `patient_slot_booking.therapist_slot_mapping_id`

### `patient_slot_booking`

Stores the child booking against a therapist slot.

Used when:

- Front office books a slot.
- Calendar shows booked child details.
- Slot is marked completed, no-show, or cancelled.
- Session balance is calculated against the selected plan item.

Key columns:

| Column | Meaning |
|---|---|
| `therapist_slot_mapping_id` | The booked therapist slot. |
| `patient_session_plan_item_id` | The child plan item/therapy allocation used for this booking. |
| `status_id` | Booking status from `patient_slot_booking_status_master`. |

Relationships:

- Booking gets child and therapy through `patient_session_plan_item`.
- Booking gets therapist/date/slot through `therapist_slot_mapping`.

### `leave_sessions`

Master table for leave session values.

Used when:

- Leave form/dropdown needs options such as first half, second half, or full day.

Key columns:

| Column | Meaning |
|---|---|
| `code` | Machine code, for example `first_half`. |
| `name` | Display name. |

### `therapist_leaves`

Stores therapist leave entries.

Used when:

- Therapist is on leave for full day, first half, or second half.
- Calendar needs to show leave in red.
- Booking should avoid leave-covered slots.

Key columns:

| Column | Meaning |
|---|---|
| `therapist_id` | Therapist on leave. |
| `leave_date` | Leave date. |
| `leave_session` | `first_half`, `second_half`, or `full_day`. |
| `reason` | Optional leave reason. |

## Session Plan And Session Tracking Tables

### `patient_session_plan`

Stores the overall session plan for a child.

Used when:

- Add Session is created from child profile or side menu.
- Booking needs to know the child has a valid plan.
- Payment/session balance is checked.

Key columns:

| Column | Meaning |
|---|---|
| `patient_id` | Child receiving the plan. |
| `plan_name` | Auto/generated plan name. |
| `total_sessions` | Total sessions in this plan. |
| `start_date`, `end_date` | Plan validity period. |
| `notes` | Optional notes. |
| `status_id` | Status from `patient_session_plan_status_master`. |

Relationships:

- `patient_session_plan.id` -> `patient_session_plan_item.patient_session_plan_id`

### `patient_session_plan_item`

Stores therapy-wise split inside a session plan.

Used when:

- Allocating sessions therapy-wise.
- Booking a slot under a specific therapy.
- Checking assigned/completed/remaining sessions.
- Showing amount per session.

Key columns:

| Column | Meaning |
|---|---|
| `patient_session_plan_id` | Parent plan. |
| `therapy_id` | Therapy type. |
| `allocated_sessions` | Sessions allowed for this therapy. |
| `assigned_sessions` | Sessions booked/assigned. |
| `completed_sessions` | Sessions completed. |
| `amount_per_session` | Therapy-wise rate. |

### `patient_therapy`

Stores a simpler direct child-to-therapy assignment.

Used when:

- The system needs to track therapies assigned to a child outside detailed session plan items.
- Older/simple therapy assignment flows are used.

Key columns:

| Column | Meaning |
|---|---|
| `patient_id` | Child. |
| `therapy_id` | Therapy. |
| `number_of_sessions` | Number of sessions assigned. |
| `status_id` | Status from `patient_therapy_status_master`. |
| `slot_id` | Optional slot reference. |
| `notes` | Therapy notes. |
| `is_active` | Active flag. |

### `appointments`

Older/general appointment table.

Used when:

- Legacy appointment APIs create or list appointments.
- A general appointment needs start/end datetime instead of slot master mapping.

Key columns:

| Column | Meaning |
|---|---|
| `patient_id` | Child. |
| `therapist_id` | Therapist. |
| `start_time`, `end_time` | Appointment datetime range. |
| `status` | Text status such as scheduled/completed/no-show. |
| `region_id` | Branch. |
| `notes` | Appointment notes. |

### `sessions`

Older clinical session tracking table.

Used when:

- A completed appointment needs session notes/progress.
- Session history is shown from legacy session records.

Key columns:

| Column | Meaning |
|---|---|
| `patient_id` | Child. |
| `therapist_id` | Therapist. |
| `appointment_id` | Optional linked appointment. |
| `session_number` | Session sequence number. |
| `duration_minutes` | Duration. |
| `status` | Session status. |
| `session_date` | Session date. |
| `progress_notes` | Therapist notes. |
| `billing_status` | Billing state. |

### `session_notes`

Stores detailed notes for a session.

Used when:

- Therapist adds clinical/progress note to a session.

Key columns:

| Column | Meaning |
|---|---|
| `session_id` | Parent session. |
| `note_type` | Type/category of note. |
| `content` | Note text. |
| `created_by` | User who wrote it. |

## Assessment Tables

### `assessment_type_master`

Master table for assessment categories.

Used when:

- Creating assessment templates.
- Categorizing `assessment_master` rows.

Key columns:

| Column | Meaning |
|---|---|
| `code`, `name` | Assessment type code and label. |
| `is_active` | Active flag. |

### `question_type_master`

Master table for question types.

Used when:

- Creating assessment questions.
- Rendering the correct answer input type.

Key columns:

| Column | Meaning |
|---|---|
| `code`, `name` | Question type code and label. |
| `is_active` | Active flag. |

### `assessment_master`

Stores assessment templates/forms.

Used when:

- Admin creates assessment type/template.
- Child assessment is assigned from a template.

Key columns:

| Column | Meaning |
|---|---|
| `title` | Assessment title. |
| `type_id` | Type from `assessment_type_master`. |
| `description` | Template description. |
| `is_active` | Whether template can be used. |

### `question_master`

Stores reusable assessment questions.

Used when:

- Assessment template is built.
- Child assessment form renders questions.

Key columns:

| Column | Meaning |
|---|---|
| `question_text` | Question text. |
| `question_type_id` | Type from `question_type_master`. |
| `is_active` | Whether question can be used. |

### `assessment_question`

Mapping table between assessment templates and questions.

Used when:

- Building an assessment form from selected questions.
- Loading all questions for a specific assessment.

Key columns:

| Column | Meaning |
|---|---|
| `assessment_id` | Assessment template. |
| `question_id` | Question included in the template. |

### `patient_assessment`

Stores an assessment assigned to a child.

Used when:

- Assessment is created for a child.
- Assessment is marked pending/completed.
- Assessment report is generated.

Key columns:

| Column | Meaning |
|---|---|
| `patient_id` | Child. |
| `assessment_id` | Assessment template. |
| `assigned_by` | User who assigned it. |
| `status_id` | Status from `patient_assessment_status_master`. |
| `completed_Date` | Completion timestamp. |
| `notes` | Assessment notes. |

### `patient_assessment_detail`

Stores answers/files for each question in a child assessment.

Used when:

- Child assessment answers are saved.
- Uploaded assessment files are attached to answers.

Key columns:

| Column | Meaning |
|---|---|
| `patient_assessment_id` | Parent child assessment. |
| `assessment_question_id` | Question in the template. |
| `answer_text` | Answer text. |
| `file_path`, `file_name`, `file_size` | Optional uploaded evidence/file. |

## Billing, Payment, And Package Tables

### `invoices`

Stores invoice header records.

Used when:

- Billing creates an invoice for a child.
- Invoice list/report is shown.
- Payment status is calculated.

Key columns:

| Column | Meaning |
|---|---|
| `invoice_number` | Unique invoice number. |
| `patient_id` | Child billed. |
| `region_id` | Branch. |
| `issue_date`, `due_date` | Invoice dates. |
| `total_amount`, `paid_amount` | Billing amounts. |
| `status_id` | Status from `invoice_status_master`. |
| `description` | Invoice note. |

### `invoice_items`

Stores line items under an invoice.

Used when:

- Invoice has multiple charges, for example therapy session fee plus assessment fee.

Key columns:

| Column | Meaning |
|---|---|
| `invoice_id` | Parent invoice. |
| `description` | Line item description. |
| `quantity` | Quantity. |
| `unit_price` | Unit price. |
| `total_price` | Quantity multiplied by unit price. |

### `payments`

Stores child payments.

Used when:

- Manual payment is recorded.
- Payment history is shown in child profile.
- Appointment popup shows payment details.
- Billing screen lists payments.

Key columns:

| Column | Meaning |
|---|---|
| `patient_id` | Paying child. |
| `payment_amount` | Amount received or pending. |
| `payment_status` | Status such as paid/pending. |
| `payment_mode` | Payment mode id. |
| `remark` | Notes. |
| `payment_date` | Date/time of payment. |

Relationships:

- `payments.payment_mode` -> `payment_mode_master.id`

### `payment_mode_master`

Master table for payment methods.

Used when:

- Payment form shows Cash/UPI/Card/etc.
- Payment records display mode name.

Key columns:

| Column | Meaning |
|---|---|
| `payment_mode_name` | Display name. |
| `description` | Optional detail. |
| `is_active` | Whether mode can be selected. |

### `packages`

Package master table.

Used when:

- Defining package offerings that can be purchased/assigned to a child.
- Setting package name, session count, price, and validity.
- Supporting the child-level Buy Package flow from the latest prototype.

Key columns:

| Column | Meaning |
|---|---|
| `name` | Package name. |
| `description` | Package details. |
| `total_sessions` | Sessions included. |
| `price` | Package price. |
| `duration_days` | Validity period. |
| `is_active` | Active flag. |

Current note:

- Package is part of the confirmed flow.
- Use this table for commercial package definitions.
- Use `patient_session_plan` and `patient_session_plan_item` for therapy-wise operational tracking after/alongside package purchase.

### `patient_packages`

Assigns purchased packages to children.

Used when:

- A child buys/is assigned a package.
- Active package status is shown in child Transactions.
- Package-level remaining sessions are tracked.
- Package start/end date and status are managed.

Key columns:

| Column | Meaning |
|---|---|
| `patient_id` | Child. |
| `package_id` | Package. |
| `start_date`, `end_date` | Validity period. |
| `sessions_completed`, `sessions_remaining` | Package balance. |
| `status` | Package status. |

Current note:

- Keep package purchase/status here.
- Keep therapy-wise allocation and per-session amount in `patient_session_plan_item`.

## Notification And Audit Tables

### `notifications`

Stores notifications for users.

Used when:

- System needs to notify a staff user.
- User notification list is shown.

Key columns:

| Column | Meaning |
|---|---|
| `user_id` | Notification receiver. |
| `title`, `message` | Notification text. |
| `notification_type` | Category. |
| `is_read` | Read/unread flag. |
| `data` | Optional JSON payload. |

### `audit_logs`

Stores audit trail for important changes.

Used when:

- Tracking create/update/delete/view operations.
- Reviewing who changed what and when.

Key columns:

| Column | Meaning |
|---|---|
| `user_id` | User who performed the action. |
| `entity_type` | Module/table name. |
| `entity_id` | Record id. |
| `action` | `CREATE`, `UPDATE`, `DELETE`, or `VIEW`. |
| `old_values`, `new_values` | JSON before/after values. |
| `ip_address`, `user_agent` | Request source. |

## Status Master Tables

These tables all share the same shape: `id`, `code`, `name`, `is_active`, `created_at`, and `updated_at`.

### `invoice_status_master`

Stores invoice statuses.

Used by:

- `invoices.status_id`

Examples:

- Draft
- Paid
- Partially paid
- Cancelled

### `patient_session_plan_status_master`

Stores session plan statuses.

Used by:

- `patient_session_plan.status_id`

Examples:

- Active
- Completed
- Cancelled

### `patient_assessment_status_master`

Stores child assessment statuses.

Used by:

- `patient_assessment.status_id`

Examples:

- Pending
- In progress
- Completed

### `patient_therapy_status_master`

Stores child therapy assignment statuses.

Used by:

- `patient_therapy.status_id`

Examples:

- Active
- Completed
- Cancelled

### `patient_slot_booking_status_master`

Stores child slot booking statuses.

Used by:

- `patient_slot_booking.status_id`

Examples:

- Booked
- Completed
- Cancelled
- No show

### `therapist_slot_mapping_status_master`

Stores therapist slot statuses.

Used by:

- `therapist_slot_mapping.status_id`

Examples:

- Available
- Booked
- Blocked
- Completed

### Main Booking Flow

1. Create/update child in `patients`.
2. Create session plan in `patient_session_plan`.
3. Add therapy-wise rows in `patient_session_plan_item`.
4. Therapist slot exists in `therapist_slot_mapping`.
5. Booking creates `patient_slot_booking`.
6. Appointment calendar joins:
   - `patient_slot_booking`
   - `therapist_slot_mapping`
   - `slot_master`
   - `therapists`
   - `therapy_master`
   - `patient_session_plan_item`
   - `patient_session_plan`
   - `patients`
7. Payment can be recorded in `payments`.
8. Completion/no-show changes `patient_slot_booking.status_id` and affects displayed session balance.
