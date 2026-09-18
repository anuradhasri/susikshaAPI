SET @has_fully_paid := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'patient_assessment_billing'
      AND COLUMN_NAME = 'fully_paid'
);

SET @add_fully_paid_sql := IF(
    @has_fully_paid = 0,
    'ALTER TABLE patient_assessment_billing ADD COLUMN fully_paid BOOLEAN NOT NULL DEFAULT 0',
    'SELECT 1'
);

PREPARE add_fully_paid_stmt FROM @add_fully_paid_sql;
EXECUTE add_fully_paid_stmt;
DEALLOCATE PREPARE add_fully_paid_stmt;

UPDATE patient_assessment_billing
SET fully_paid = CASE WHEN due_amount <= 0 THEN 1 ELSE 0 END;
