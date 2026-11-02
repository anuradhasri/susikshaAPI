ALTER TABLE patient_observation_sheet_entry
  ADD COLUMN created_by INT NULL,
  ADD COLUMN created_at DATETIME NULL;
UPDATE patient_observation_sheet_entry e
JOIN patient_observation_sheet s ON s.id = e.sheet_id
SET e.created_by = s.created_by, e.created_at = s.created_at;
ALTER TABLE patient_observation_sheet_entry
  MODIFY created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
  ADD CONSTRAINT fk_observation_entry_creator FOREIGN KEY (created_by) REFERENCES users(id);
