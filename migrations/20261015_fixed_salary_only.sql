-- Effective-dated payroll flag for therapists who must not earn session payouts.
ALTER TABLE therapist_payroll_config
    ADD COLUMN fixed_salary_only BOOLEAN NOT NULL DEFAULT 0 AFTER professional_tax;
