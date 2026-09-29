CREATE TABLE IF NOT EXISTS patient_goal_summary (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  patient_id INT NOT NULL,
  region_id INT NOT NULL,
  evaluation_date DATE NOT NULL,
  re_evaluation_date DATE NULL,
  therapist_id INT NOT NULL,
  informant VARCHAR(255) NOT NULL DEFAULT '',
  level_id INT NOT NULL,
  review_date DATE NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by INT NOT NULL,
  updated_by INT NOT NULL,
  KEY idx_goal_summary_patient (patient_id),
  KEY idx_goal_summary_region (region_id),
  KEY idx_goal_summary_date (evaluation_date),
  KEY idx_goal_summary_therapist (therapist_id),
  KEY idx_goal_summary_level (level_id),
  CONSTRAINT fk_goal_summary_patient FOREIGN KEY (patient_id) REFERENCES patients(id),
  CONSTRAINT fk_goal_summary_region FOREIGN KEY (region_id) REFERENCES regions(id),
  CONSTRAINT fk_goal_summary_therapist FOREIGN KEY (therapist_id) REFERENCES therapists(id),
  CONSTRAINT fk_goal_summary_level FOREIGN KEY (level_id) REFERENCES goal_level_master(id),
  CONSTRAINT fk_goal_summary_created_by FOREIGN KEY (created_by) REFERENCES users(id),
  CONSTRAINT fk_goal_summary_updated_by FOREIGN KEY (updated_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS patient_goal_summary_response (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  summary_id INT NOT NULL,
  skill_id INT NOT NULL,
  status VARCHAR(2) NOT NULL DEFAULT 'NO',
  comments TEXT NOT NULL,
  KEY idx_goal_summary_response_summary (summary_id),
  KEY idx_goal_summary_response_skill (skill_id),
  UNIQUE KEY uq_goal_summary_response (summary_id, skill_id),
  CONSTRAINT fk_goal_summary_response_summary FOREIGN KEY (summary_id) REFERENCES patient_goal_summary(id) ON DELETE CASCADE,
  CONSTRAINT fk_goal_summary_response_skill FOREIGN KEY (skill_id) REFERENCES goal_skill_master(id)
);
