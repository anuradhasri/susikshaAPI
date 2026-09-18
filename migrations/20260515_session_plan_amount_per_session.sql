ALTER TABLE patient_session_plan_item
  ADD COLUMN amount_per_session DECIMAL(10,2) NOT NULL DEFAULT 0.00;

ALTER TABLE patient_session_plan
  ADD COLUMN notes TEXT NULL;
