ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS program_id INT NULL,
  ADD COLUMN IF NOT EXISTS appointment_date DATE NULL;

CREATE TABLE IF NOT EXISTS appointment_child_mapping (
  id INT AUTO_INCREMENT PRIMARY KEY,
  appointment_id INT NOT NULL,
  child_id INT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_appointment_child (appointment_id, child_id),
  KEY idx_appointment_child_appointment_id (appointment_id),
  KEY idx_appointment_child_child_id (child_id),
  CONSTRAINT fk_appointment_child_appointment FOREIGN KEY (appointment_id) REFERENCES appointments(id),
  CONSTRAINT fk_appointment_child_child FOREIGN KEY (child_id) REFERENCES patients(id)
);

CREATE TABLE IF NOT EXISTS appointment_therapist_slot (
  id INT AUTO_INCREMENT PRIMARY KEY,
  appointment_id INT NOT NULL,
  therapist_id INT NOT NULL,
  therapy_id INT NOT NULL,
  start_time DATETIME NOT NULL,
  end_time DATETIME NOT NULL,
  duration_minutes INT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_appointment_therapist_slot_appointment_id (appointment_id),
  KEY idx_appointment_therapist_slot_therapist_time (therapist_id, start_time, end_time),
  KEY idx_appointment_therapist_slot_therapy_id (therapy_id),
  CONSTRAINT fk_appointment_therapist_slot_appointment FOREIGN KEY (appointment_id) REFERENCES appointments(id),
  CONSTRAINT fk_appointment_therapist_slot_therapist FOREIGN KEY (therapist_id) REFERENCES therapists(id),
  CONSTRAINT fk_appointment_therapist_slot_therapy FOREIGN KEY (therapy_id) REFERENCES therapy_master(id)
);

ALTER TABLE assessment_master
  ADD COLUMN IF NOT EXISTS assessment_name VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS display_order INT NOT NULL DEFAULT 0;

ALTER TABLE assessment_question
  MODIFY COLUMN question_id INT NULL,
  ADD COLUMN IF NOT EXISTS question_text TEXT NULL,
  ADD COLUMN IF NOT EXISTS question_type VARCHAR(50) NOT NULL DEFAULT 'textarea',
  ADD COLUMN IF NOT EXISTS display_order INT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS assessment_question_option (
  id INT AUTO_INCREMENT PRIMARY KEY,
  question_id INT NOT NULL,
  option_text VARCHAR(255) NOT NULL,
  display_order INT NOT NULL DEFAULT 0,
  KEY idx_assessment_question_option_question_id (question_id),
  CONSTRAINT fk_assessment_question_option_question FOREIGN KEY (question_id) REFERENCES assessment_question(id)
);

CREATE TABLE IF NOT EXISTS therapy_assessment_mapping (
  id INT AUTO_INCREMENT PRIMARY KEY,
  therapy_id INT NOT NULL,
  assessment_id INT NOT NULL,
  can_edit TINYINT(1) NOT NULL DEFAULT 0,
  UNIQUE KEY uq_therapy_assessment (therapy_id, assessment_id),
  CONSTRAINT fk_therapy_assessment_therapy FOREIGN KEY (therapy_id) REFERENCES therapy_master(id),
  CONSTRAINT fk_therapy_assessment_assessment FOREIGN KEY (assessment_id) REFERENCES assessment_master(id)
);

CREATE TABLE IF NOT EXISTS child_assessment_answer (
  id INT AUTO_INCREMENT PRIMARY KEY,
  child_id INT NOT NULL,
  assessment_id INT NOT NULL,
  question_id INT NOT NULL,
  answer_value TEXT NULL,
  created_by INT NULL,
  updated_by INT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_child_assessment_question_answer (child_id, question_id),
  KEY idx_child_assessment_answer_child_id (child_id),
  CONSTRAINT fk_child_assessment_answer_child FOREIGN KEY (child_id) REFERENCES patients(id),
  CONSTRAINT fk_child_assessment_answer_assessment FOREIGN KEY (assessment_id) REFERENCES assessment_master(id),
  CONSTRAINT fk_child_assessment_answer_question FOREIGN KEY (question_id) REFERENCES assessment_question(id)
);
