# Therapist Payout Logic by Program

This document describes the therapist payout behavior implemented in the backend as of 16 October 2026. It covers configuration, session eligibility, payment-based earnings, program-specific formulas, payroll generation, tax, adjustments, and finalization.

## 1. Core principles

1. Payout rules are configured per therapist and per program.
2. Rules are effective-dated. The rule applicable on the session or payment date is used.
3. A completed session does not automatically mean money is payable. Earnings are proportional to the amount actually collected or covered by package credit.
4. Amounts are rounded to two decimal places.
5. Approved or paid monthly payroll is treated as final and is not recalculated by synchronization.
6. A therapist marked `fixed_salary_only` receives no session-based earnings.

## 2. Program identification

Special payout behavior is selected using the program name:

| Program name contains | Applied logic |
|---|---|
| `CRT` or `Structured` | Duration-based Structured Program payout |
| `Vocational` | Fixed ₹529 payout for the whole session |
| `Parent` | 40% therapist-pool logic |
| Anything else | Configured percentage, fixed, package, or completion rule |

Program naming must therefore remain consistent. Renaming a program without these identifying words may cause the general calculation to be used.

## 3. Configuration

### 3.1 Program payment rule

Each therapist/program combination can store:

- Payment method: `percentage`, `fixed`, `package`, or `completion`
- Therapist percentage
- Fixed amount
- GST rate
- Therapist pool percentage
- Release-on-completion flag
- Effective-from and effective-to dates

When a new effective-dated rule is created, the previous active rule is closed on the preceding date. Historical sessions use the rule effective on their date. If no historical rule existed, the earliest active rule is used so that old sessions remain reportable.

### 3.2 Patient-specific pricing

If a child has program-specific pricing, it overrides the default booking amount:

- Per-session pricing: `eligible amount = agreed amount`
- Package pricing: `eligible amount = agreed package amount ÷ package sessions`

For a normal patient package, the per-session value is similarly:

`package session amount = package price ÷ total package sessions`

### 3.3 Payroll rule

A therapist can have an effective-dated fixed-salary payroll rule. When `fixed_salary_only = true`:

- Session earnings are forced to ₹0.
- The monthly base salary is used.
- Per-program rules are not required for the therapist to appear in billing reports.

## 4. Common calculation terms

### 4.1 Session amount and received amount

- `session amount` is the billable value of the session.
- `session received` is cash received plus applicable package-covered value.
- Received value is capped at the session amount.

### 4.2 Collection ratio

`collection ratio = min(1, session received ÷ session amount)`

Examples:

- ₹1,250 received against ₹1,250 → ratio `1.00`
- ₹625 received against ₹1,250 → ratio `0.50`
- Nothing received → ratio `0.00`

The collection ratio prevents the therapist from earning the full payout before the organization receives the full session value.

### 4.3 Inclusive GST

For GST-enabled programs, GST is extracted from the collected amount:

`GST = received × GST rate ÷ (100 + GST rate)`

`amount after GST = received − GST`

Example for ₹1,180 inclusive of 18% GST:

- GST = ₹180
- Amount after GST = ₹1,000

Structured and Vocational programs always use 0% GST in the payout calculation.

## 5. Program-specific payout formulas

### 5.1 Structured Program / CRT

A Structured session contains three therapist segments but is one child/package session:

- First 45-minute segment
- Second 45-minute segment
- One 30-minute segment

Package consumption and child transactions count the combined 45 + 45 + 30 minutes as one session. Therapist earnings remain segment-based because each segment can have a different therapist.

Default rates:

| Segment duration | Default therapist earning |
|---|---:|
| 45 minutes | ₹300 |
| 30 minutes | ₹100 |

The 45-minute rate comes from `fixed_amount`. The 30-minute rate comes from `therapist_pool_percentage`. These values are configurable per therapist and program.

Formula:

`therapist earning = configured duration rate × collection ratio`

Example with full collection:

- Therapist A, 45 minutes → ₹300
- Therapist B, 45 minutes → ₹300
- Therapist C, 30 minutes → ₹100
- Total therapist cost → ₹700

Example at 50% collection:

- ₹150 + ₹150 + ₹50 = ₹350 total therapist earnings

No GST is deducted for Structured/CRT payout calculations.

### 5.2 Vocational Program

The child-facing session amount is derived from the package:

`session amount = package total ÷ number of sessions`

Example:

`₹10,000 ÷ 8 sessions = ₹1,250 per session`

Therapist payout is not a percentage of ₹1,250. It is fixed at ₹529 for the whole Vocational session.

Formula:

`earning per therapist = ₹529 × collection ratio ÷ attending therapist count`

Examples:

- One therapist, full collection → ₹529
- Two therapists, full collection → ₹264.50 each
- One therapist, 50% collection → ₹264.50

No GST is deducted for Vocational payout calculations. The organization amount is the collected amount after GST (which is the full eligible amount here) minus total therapist earnings.

### 5.3 Parent Program

Parent Program pricing is per child. When a parent-program group defines a pro-rata amount:

`per-child session value = group pro-rata amount ÷ number of sessions`

The total session value is the sum of the participating children's session values. Actual collected money is allocated equally across attending therapists before applying the therapist's configured share.

After inclusive GST is removed:

- Organization pool: 60%
- Therapist pool: 40%

Each therapist has a configured share of the 40% therapist pool.

Formula:

`therapist earning = amount after GST × 40% × configured therapist share%`

Example using ₹1,180 collected, inclusive of 18% GST:

- Amount after GST = ₹1,000
- Therapist pool = ₹400
- Therapist configured at 50% of pool → ₹200
- Remaining post-GST amount stays with the organization

For an active Parent Program period, earnings remain `pending_completion` until the group is completed or its end date has passed.

### 5.4 General percentage method

Formula:

`therapist earning = amount after GST × configured percentage`

Example:

- Collected = ₹1,180
- GST = 18%, so amount after GST = ₹1,000
- Therapist percentage = 40%
- Therapist earning = ₹400

### 5.5 General fixed method

Formula:

`therapist earning = configured fixed amount × collection ratio`

Example:

- Fixed earning = ₹500
- 60% of session value collected
- Therapist earning = ₹300

### 5.6 General package method

First determine the package per-session value, then apply GST and the configured therapist-pool share.

Formula:

`therapist earning = amount after GST × therapist pool percentage`

The default therapist pool percentage is 50% when no value is configured.

### 5.7 Completion method

The calculation uses either the configured fixed amount or percentage. If `release_on_completion` is enabled, collected earnings remain `pending_completion` until the package is complete.

## 6. Multiple therapists and group sessions

For group-program payments, attending therapists are identified from the linked group booking.

- Cash received is divided across the attending therapists.
- Allocation percentage is stored as `100 ÷ therapist count`.
- Child equivalent is `number of children ÷ therapist count`.
- Vocational ₹529 is divided equally across attending therapists.
- Parent Program collection is divided across therapists before applying each therapist's configured share of the 40% pool.

Only the canonical payout row for a therapist/group session is eligible; duplicate child/slot rows are not separately paid.

## 7. Payment-date earning ledger

Positive payment installments can generate `therapist_payment_earning` records. This means partial payments can produce proportional earnings on the date money is received.

One earning is stored per:

- Payment
- Therapist

If the same payment is synchronized again, the existing earning is updated rather than duplicated. Earnings linked to approved or paid monthly payroll are not modified.

Monthly generation avoids double-counting: when a payment-ledger earning exists for the same booking and therapist, the older session-payout row is excluded.

## 8. Required sheets

Programs can require one or more sheets, including:

- Observation sheet
- Goal sheet
- Goal summary

When a completed session is synchronized, an assignment is created for each required sheet. Existing submitted sheets are linked automatically. Sheet assignment and submission status are visible in the payout dashboard.

Current calculation behavior records payout eligibility independently from sheet submission. Sheet configuration currently drives assignment and tracking; it does not directly set the payout amount to zero.

## 9. Eligibility statuses

| Status | Meaning |
|---|---|
| `eligible` | Collected earning can be included in monthly payroll |
| `pending_payment` | No session payment/package collection has been received |
| `pending_completion` | Payment exists, but package/program completion is required |

Sessions without a therapist/program payment configuration are skipped, except fixed-salary-only therapists, for whom an internal zero-value rule can be created.

## 10. Monthly payroll

For each therapist and month:

1. Collect eligible session-payout rows not already represented by payment earnings.
2. Add eligible payment-date earning rows.
3. If fixed-salary-only, discard session earnings and use base salary.
4. Calculate professional tax.
5. Apply approved positive or negative adjustments.

For session-based therapists:

`gross before tax = total eligible session/payment earnings`

For fixed-salary-only therapists:

`gross before tax = base salary`

Then:

`gross after tax = gross before tax − professional tax`

`payable amount = gross after tax + adjustments`

An adjustment cannot reduce payable salary below ₹0.

## 11. Professional tax

Professional tax is applied only when gross income is greater than the configured income threshold.

Default settings when no saved settings row exists:

- Income threshold: ₹25,000
- Regular month tax: ₹200
- March tax: ₹300

Formula:

- Gross income ≤ threshold → ₹0 tax
- Gross income > threshold and month is not March → regular-month amount
- Gross income > threshold and month is March → March amount

## 12. Payroll lifecycle

Monthly payout statuses:

1. `draft` — recalculation and adjustments are allowed
2. `approved` — finalized financial values; synchronization no longer changes linked earnings
3. `paid` — payment date/reference is recorded; adjustments are blocked

## 13. Main database records

| Table | Purpose |
|---|---|
| `patient_program_pricing` | Child-specific program/package pricing |
| `program_payment_config` | Effective-dated therapist/program payout rules |
| `program_payment_config_history` | Audit history of payout rules |
| `program_sheet_config` | Required sheets by program |
| `therapy_sheet_assignment` | Sheet required for a completed session |
| `therapy_sheet_submission` | Submitted sheet linked to an assignment |
| `therapist_session_allocation` | Therapist share and child-equivalent allocation |
| `therapist_session_payout` | Session-date payout calculation |
| `therapist_payment_earning` | Payment-date installment earning |
| `therapist_monthly_payout` | Monthly payroll summary |
| `therapist_payout_adjustment` | Manual payroll adjustment and reason |
| `therapist_payroll_config` | Fixed-salary-only setup |
| `payroll_tax_settings` | Professional tax threshold and amounts |

## 14. Operational checklist

Before generating therapist payroll:

1. Confirm therapist/program payout rules and effective dates.
2. Confirm child-specific pricing or package pricing.
3. Complete the session and record payment/package coverage.
4. Synchronize the required date range.
5. Review pending sheets and pending-payment/completion rows.
6. Generate the monthly payroll.
7. Review professional tax and adjustments.
8. Approve payroll.
9. Mark payroll paid with its payment reference.

