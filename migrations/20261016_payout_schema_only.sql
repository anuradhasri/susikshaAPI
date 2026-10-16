-- Schema-only migration for payout, payroll, pricing, and sheet tracking.
-- Intentionally contains no INSERT, UPDATE, or DELETE statements.

CREATE TABLE IF NOT EXISTS patient_program_pricing (
  id INT AUTO_INCREMENT PRIMARY KEY, patient_id INT NOT NULL, program_id INT NOT NULL,
  billing_type VARCHAR(24) NOT NULL, agreed_amount FLOAT NOT NULL, total_sessions INT NULL,
  effective_from DATE NOT NULL, effective_to DATE NULL, is_active BOOLEAN NOT NULL DEFAULT 1,
  created_by INT NULL, updated_by INT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL,
  UNIQUE KEY uq_patient_program_pricing_effective (patient_id, program_id, effective_from),
  KEY idx_patient_program_pricing_lookup (patient_id, program_id, effective_from, effective_to)
);

CREATE TABLE IF NOT EXISTS program_payment_config (id INT AUTO_INCREMENT PRIMARY KEY, therapist_id INT NOT NULL, program_id INT NOT NULL, payment_method VARCHAR(32) NOT NULL, percentage DOUBLE NULL, fixed_amount DOUBLE NULL, gst_rate DOUBLE NOT NULL DEFAULT 18, therapist_pool_percentage DOUBLE NULL, allocation_rule VARCHAR(32) NOT NULL DEFAULT 'equal_child_equivalent', release_on_completion BOOLEAN NOT NULL DEFAULT 0, effective_from DATE NOT NULL, effective_to DATE NULL, is_active BOOLEAN NOT NULL DEFAULT 1, created_by INT NULL, updated_by INT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL, UNIQUE KEY uq_therapist_program_payment_effective_date(therapist_id,program_id,effective_from), KEY idx_program_payment_lookup(therapist_id,program_id,effective_from,effective_to));
CREATE TABLE IF NOT EXISTS program_payment_config_history (id INT AUTO_INCREMENT PRIMARY KEY, config_id INT NOT NULL, action VARCHAR(24) NOT NULL, snapshot_json TEXT NOT NULL, changed_by INT NULL, changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, KEY idx_payment_config_history(config_id));
CREATE TABLE IF NOT EXISTS program_sheet_config (id INT AUTO_INCREMENT PRIMARY KEY, program_id INT NOT NULL, sheet_type VARCHAR(32) NOT NULL, is_required BOOLEAN NOT NULL DEFAULT 1, effective_from DATE NOT NULL, effective_to DATE NULL, is_active BOOLEAN NOT NULL DEFAULT 1, created_by INT NULL, updated_by INT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL, UNIQUE KEY uq_program_sheet_effective_date(program_id,sheet_type,effective_from), KEY idx_program_sheet_lookup(program_id,effective_from,effective_to));
CREATE TABLE IF NOT EXISTS therapy_sheet_assignment (id INT AUTO_INCREMENT PRIMARY KEY, patient_slot_booking_id INT NOT NULL, patient_id INT NOT NULL, therapist_id INT NOT NULL, program_id INT NULL, sheet_type VARCHAR(32) NOT NULL DEFAULT 'observation', assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, due_at DATETIME NULL, status VARCHAR(24) NOT NULL DEFAULT 'pending', notified_at DATETIME NULL, notification_read_at DATETIME NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL, UNIQUE KEY uq_session_therapist_sheet(patient_slot_booking_id,therapist_id,sheet_type), KEY idx_sheet_assignment_status(status));
CREATE TABLE IF NOT EXISTS therapy_sheet_submission (id INT AUTO_INCREMENT PRIMARY KEY, assignment_id INT NOT NULL UNIQUE, submitted_by INT NOT NULL, submitted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, source_table VARCHAR(64) NULL, source_id INT NULL, notes TEXT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL);
CREATE TABLE IF NOT EXISTS therapist_session_allocation (id INT AUTO_INCREMENT PRIMARY KEY, patient_slot_booking_id INT NOT NULL, therapist_id INT NOT NULL, child_equivalent DOUBLE NOT NULL DEFAULT 1, allocation_percentage DOUBLE NOT NULL DEFAULT 100, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL, UNIQUE KEY uq_session_therapist_allocation(patient_slot_booking_id,therapist_id));
CREATE TABLE IF NOT EXISTS therapist_monthly_payout (id INT AUTO_INCREMENT PRIMARY KEY, therapist_id INT NOT NULL, payout_month DATE NOT NULL, gross_amount DOUBLE NOT NULL, adjustment_amount DOUBLE NOT NULL DEFAULT 0, payable_amount DOUBLE NOT NULL, status VARCHAR(24) NOT NULL DEFAULT 'draft', approved_by INT NULL, approved_at DATETIME NULL, paid_at DATETIME NULL, payment_reference VARCHAR(128) NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL, UNIQUE KEY uq_therapist_monthly_payout(therapist_id,payout_month));
CREATE TABLE IF NOT EXISTS therapist_session_payout (id INT AUTO_INCREMENT PRIMARY KEY, patient_slot_booking_id INT NOT NULL, therapist_id INT NOT NULL, program_id INT NOT NULL, config_id INT NOT NULL, session_date DATE NOT NULL, eligible_amount DOUBLE NOT NULL, gst_amount DOUBLE NOT NULL, amount_after_gst DOUBLE NOT NULL, therapist_percentage DOUBLE NULL, therapist_amount DOUBLE NOT NULL, organization_amount DOUBLE NOT NULL, calculation_json TEXT NOT NULL, eligibility_status VARCHAR(24) NOT NULL DEFAULT 'eligible', monthly_payout_id INT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL, UNIQUE KEY uq_session_therapist_payout(patient_slot_booking_id,therapist_id), KEY idx_session_payout_month(session_date,monthly_payout_id));
CREATE TABLE IF NOT EXISTS therapist_program_payout (id INT AUTO_INCREMENT PRIMARY KEY, therapist_id INT NOT NULL, program_id INT NOT NULL, patient_package_id INT NULL, config_id INT NOT NULL, earned_date DATE NULL, eligible_amount DOUBLE NOT NULL DEFAULT 0, therapist_amount DOUBLE NOT NULL DEFAULT 0, status VARCHAR(24) NOT NULL DEFAULT 'pending', calculation_json TEXT NOT NULL, monthly_payout_id INT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL, KEY idx_program_payout_month(monthly_payout_id));
CREATE TABLE IF NOT EXISTS therapist_payout_adjustment (id INT AUTO_INCREMENT PRIMARY KEY, monthly_payout_id INT NOT NULL, amount DOUBLE NOT NULL, reason TEXT NOT NULL, approved_by INT NULL, approved_at DATETIME NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL, KEY idx_payout_adjustment_monthly(monthly_payout_id));

CREATE TABLE IF NOT EXISTS therapist_payroll_config (
  id INT AUTO_INCREMENT PRIMARY KEY, therapist_id INT NOT NULL, base_salary FLOAT NOT NULL DEFAULT 0,
  flat_commission FLOAT NOT NULL DEFAULT 0, professional_tax FLOAT NOT NULL DEFAULT 0,
  fixed_salary_only BOOLEAN NOT NULL DEFAULT 0, effective_from DATE NOT NULL, effective_to DATE NULL,
  is_active BOOLEAN NOT NULL DEFAULT 1, created_by INT NULL, updated_by INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL,
  UNIQUE KEY uq_therapist_payroll_effective_date (therapist_id, effective_from),
  KEY idx_therapist_payroll_lookup (therapist_id, effective_from, effective_to)
);

CREATE TABLE IF NOT EXISTS payroll_tax_settings (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, income_threshold FLOAT NOT NULL DEFAULT 25000,
  regular_month_amount FLOAT NOT NULL DEFAULT 200, march_amount FLOAT NOT NULL DEFAULT 300,
  updated_by INT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL
);

CREATE TABLE IF NOT EXISTS therapist_payment_earning (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, payment_id BIGINT NOT NULL,
  patient_slot_booking_id INT NOT NULL, therapist_id INT NOT NULL, program_id INT NOT NULL,
  config_id INT NOT NULL, earning_date DATE NOT NULL, received_amount FLOAT NOT NULL DEFAULT 0,
  gst_amount FLOAT NOT NULL DEFAULT 0, amount_after_gst FLOAT NOT NULL DEFAULT 0,
  therapist_amount FLOAT NOT NULL DEFAULT 0, organization_amount FLOAT NOT NULL DEFAULT 0,
  calculation_json TEXT NOT NULL, status VARCHAR(24) NOT NULL DEFAULT 'eligible', monthly_payout_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME NULL,
  UNIQUE KEY uq_payment_therapist_earning (payment_id, therapist_id),
  KEY ix_payment_earning_date (earning_date), KEY ix_payment_earning_therapist (therapist_id),
  KEY ix_payment_earning_monthly (monthly_payout_id)
);

CREATE TABLE IF NOT EXISTS patient_goal_summary_response (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, summary_id INT NOT NULL, skill_id INT NOT NULL,
  status VARCHAR(2) NOT NULL DEFAULT 'NO', comments TEXT NOT NULL,
  KEY idx_goal_summary_response_summary (summary_id), KEY idx_goal_summary_response_skill (skill_id),
  UNIQUE KEY uq_goal_summary_response (summary_id, skill_id),
  CONSTRAINT fk_goal_summary_response_summary FOREIGN KEY (summary_id) REFERENCES patient_goal_summary(id) ON DELETE CASCADE,
  CONSTRAINT fk_goal_summary_response_skill FOREIGN KEY (skill_id) REFERENCES goal_skill_master(id)
);
