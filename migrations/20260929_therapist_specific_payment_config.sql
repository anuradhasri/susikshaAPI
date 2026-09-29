-- Apply only when program_payment_config was created by an earlier payout migration.
ALTER TABLE program_payment_config ADD COLUMN therapist_id INT NULL AFTER id;
ALTER TABLE program_payment_config DROP INDEX uq_program_payment_effective_date;
ALTER TABLE program_payment_config ADD UNIQUE KEY uq_therapist_program_payment_effective_date (therapist_id, program_id, effective_from);
ALTER TABLE program_payment_config DROP INDEX idx_program_payment_lookup;
ALTER TABLE program_payment_config ADD KEY idx_program_payment_lookup (therapist_id, program_id, effective_from, effective_to);
