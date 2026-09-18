INSERT INTO roles (name, description)
SELECT 'therapist', 'Therapist read-only access to own appointments'
WHERE NOT EXISTS (
  SELECT 1 FROM roles WHERE name = 'therapist'
);

INSERT INTO roles (name, description)
SELECT 'central_head', 'Central head read-only access across assigned centres'
WHERE NOT EXISTS (
  SELECT 1 FROM roles WHERE name = 'central_head'
);

SET @has_therapist_user_id = (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'therapists'
    AND column_name = 'user_id'
);
SET @add_therapist_user_id = IF(
  @has_therapist_user_id = 0,
  'ALTER TABLE therapists ADD COLUMN user_id INT NULL',
  'SELECT 1'
);
PREPARE add_therapist_user_id_stmt FROM @add_therapist_user_id;
EXECUTE add_therapist_user_id_stmt;
DEALLOCATE PREPARE add_therapist_user_id_stmt;

SET @has_therapist_user_id_index = (
  SELECT COUNT(*)
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'therapists'
    AND index_name = 'idx_therapist_user_id'
);
SET @add_therapist_user_id_index = IF(
  @has_therapist_user_id_index = 0,
  'CREATE INDEX idx_therapist_user_id ON therapists (user_id)',
  'SELECT 1'
);
PREPARE add_therapist_user_id_index_stmt FROM @add_therapist_user_id_index;
EXECUTE add_therapist_user_id_index_stmt;
DEALLOCATE PREPARE add_therapist_user_id_index_stmt;
