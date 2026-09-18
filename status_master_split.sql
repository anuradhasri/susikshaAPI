CREATE TABLE IF NOT EXISTS invoice_status_master (
  id INT PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patient_session_plan_status_master (
  id INT PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patient_assessment_status_master (
  id INT PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patient_therapy_status_master (
  id INT PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patient_slot_booking_status_master (
  id INT PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS therapist_slot_mapping_status_master (
  id INT PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assessment_type_master (
  id INT PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS question_type_master (
  id INT PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT IGNORE INTO invoice_status_master (id, code, name, is_active)
VALUES
  (301, 'draft', 'Draft', TRUE),
  (302, 'issued', 'Issued', TRUE),
  (303, 'overdue', 'Overdue', TRUE),
  (304, 'paid', 'Paid', TRUE),
  (305, 'cancelled', 'Cancelled', TRUE);

INSERT IGNORE INTO patient_session_plan_status_master (id, code, name, is_active)
VALUES
  (401, 'ACTIVE', 'Active', TRUE),
  (402, 'CANCELLED', 'Cancelled', TRUE),
  (403, 'COMPLETED', 'Completed', TRUE);

INSERT IGNORE INTO patient_assessment_status_master (id, code, name, is_active)
VALUES
  (501, 'PENDING', 'Pending', TRUE),
  (502, 'IN_PROGRESS', 'In Progress', TRUE),
  (503, 'COMPLETED', 'Completed', TRUE),
  (504, 'CANCELLED', 'Cancelled', TRUE);

INSERT IGNORE INTO patient_therapy_status_master (id, code, name, is_active)
VALUES
  (701, 'ACTIVE', 'Active', TRUE),
  (702, 'COMPLETED', 'Completed', TRUE),
  (703, 'ON_HOLD', 'On Hold', TRUE),
  (704, 'CANCELLED', 'Cancelled', TRUE);

INSERT IGNORE INTO patient_slot_booking_status_master (id, code, name, is_active)
VALUES
  (601, 'BOOKED', 'Booked', TRUE),
  (602, 'CANCELLED', 'Cancelled', TRUE),
  (603, 'COMPLETED', 'Completed', TRUE),
  (604, 'NO_SHOW', 'No Show', TRUE);

INSERT IGNORE INTO therapist_slot_mapping_status_master (id, code, name, is_active)
VALUES
  (801, 'ASSIGNED', 'Assigned', TRUE),
  (802, 'BOOKED', 'Booked', TRUE),
  (803, 'COMPLETED', 'Completed', TRUE),
  (804, 'CANCELLED', 'Cancelled', TRUE);

INSERT IGNORE INTO assessment_type_master (id, code, name, is_active)
VALUES
  (901, 'STRUCTURED', 'Structured', TRUE),
  (902, 'UNSTRUCTURED', 'Unstructured', TRUE);

INSERT IGNORE INTO question_type_master (id, code, name, is_active)
VALUES
  (1001, 'TEXT', 'Text', TRUE),
  (1002, 'MCQ', 'MCQ', TRUE),
  (1003, 'FILE_UPLOAD', 'File Upload', TRUE),
  (1004, 'RATING', 'Rating', TRUE),
  (1005, 'YES_NO', 'Yes/No', TRUE);
