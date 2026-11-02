-- MySQL: permanently remove selected children and all child-owned history.
-- Replace ONLY the example IDs below.
-- This script defaults to ROLLBACK so the first run is a safe preview.

DROP TEMPORARY TABLE IF EXISTS delete_patient_ids;
CREATE TEMPORARY TABLE delete_patient_ids (
    patient_id INT PRIMARY KEY
);

-- Example: replace 122 and 128 with the child IDs you want to remove.
INSERT INTO delete_patient_ids (patient_id) VALUES
    (120);

-- Capture the child's linked therapist-slot mappings before bookings are removed.
DROP TEMPORARY TABLE IF EXISTS delete_therapist_slot_mapping_ids;
CREATE TEMPORARY TABLE delete_therapist_slot_mapping_ids (
    mapping_id INT PRIMARY KEY
);

INSERT IGNORE INTO delete_therapist_slot_mapping_ids (mapping_id)
SELECT DISTINCT b.therapist_slot_mapping_id
FROM patient_slot_booking b
JOIN delete_patient_ids d ON d.patient_id = b.patient_id
WHERE b.therapist_slot_mapping_id IS NOT NULL;

-- Verify the exact children selected before any deletion.
SELECT p.id, p.first_name, p.last_name
FROM patients p
JOIN delete_patient_ids d ON d.patient_id = p.id
ORDER BY p.id;

START TRANSACTION;

-- Payout and submitted-sheet rows linked through booked slots/payments.
DELETE s
FROM therapy_sheet_submission s
JOIN therapy_sheet_assignment a ON a.id = s.assignment_id
JOIN delete_patient_ids d ON d.patient_id = a.patient_id;

DELETE e
FROM therapist_payment_earning e
JOIN payments p ON p.id = e.payment_id
JOIN delete_patient_ids d ON d.patient_id = p.patient_id;

DELETE e
FROM therapist_payment_earning e
JOIN patient_slot_booking b ON b.id = e.patient_slot_booking_id
JOIN delete_patient_ids d ON d.patient_id = b.patient_id;

DELETE p
FROM therapist_session_payout p
JOIN patient_slot_booking b ON b.id = p.patient_slot_booking_id
JOIN delete_patient_ids d ON d.patient_id = b.patient_id;

DELETE a
FROM therapist_session_allocation a
JOIN patient_slot_booking b ON b.id = a.patient_slot_booking_id
JOIN delete_patient_ids d ON d.patient_id = b.patient_id;

DELETE p
FROM therapist_program_payout p
JOIN patient_packages pp ON pp.id = p.patient_package_id
JOIN delete_patient_ids d ON d.patient_id = pp.patient_id;

DELETE a
FROM therapy_sheet_assignment a
JOIN delete_patient_ids d ON d.patient_id = a.patient_id;

-- Assessment records.
DELETE b
FROM patient_assessment_billing b
JOIN delete_patient_ids d ON d.patient_id = b.patient_id;

DELETE ad
FROM patient_assessment_detail ad
JOIN patient_assessment pa ON pa.id = ad.patient_assessment_id
JOIN delete_patient_ids d ON d.patient_id = pa.patient_id;

DELETE pa
FROM patient_assessment pa
JOIN delete_patient_ids d ON d.patient_id = pa.patient_id;

DELETE ca
FROM child_assessment_answer ca
JOIN delete_patient_ids d ON d.patient_id = ca.child_id;

-- Goal sheets and their child rows.
DELETE x
FROM patient_goal_sheet_item_domain x
JOIN patient_goal_sheet_item i ON i.id = x.item_id
JOIN patient_goal_sheet s ON s.id = i.sheet_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE x
FROM patient_goal_sheet_item_objective x
JOIN patient_goal_sheet_item i ON i.id = x.item_id
JOIN patient_goal_sheet s ON s.id = i.sheet_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE x
FROM patient_goal_sheet_item_skill x
JOIN patient_goal_sheet_item i ON i.id = x.item_id
JOIN patient_goal_sheet s ON s.id = i.sheet_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE t
FROM patient_goal_sheet_therapist t
JOIN patient_goal_sheet s ON s.id = t.sheet_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE i
FROM patient_goal_sheet_item i
JOIN patient_goal_sheet s ON s.id = i.sheet_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE s
FROM patient_goal_sheet s
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

-- Goal summaries and their child rows.
DELETE r
FROM patient_goal_summary_response r
JOIN patient_goal_summary s ON s.id = r.summary_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE t
FROM patient_goal_summary_therapist t
JOIN patient_goal_summary s ON s.id = t.summary_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE s
FROM patient_goal_summary s
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

-- Observation sheets and their child rows.
DELETE x
FROM patient_observation_sheet_entry_domain x
JOIN patient_observation_sheet_entry e ON e.id = x.entry_id
JOIN patient_observation_sheet s ON s.id = e.sheet_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE x
FROM patient_observation_sheet_entry_skill x
JOIN patient_observation_sheet_entry e ON e.id = x.entry_id
JOIN patient_observation_sheet s ON s.id = e.sheet_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE t
FROM patient_observation_sheet_therapist t
JOIN patient_observation_sheet s ON s.id = t.sheet_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE e
FROM patient_observation_sheet_entry e
JOIN patient_observation_sheet s ON s.id = e.sheet_id
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

DELETE s
FROM patient_observation_sheet s
JOIN delete_patient_ids d ON d.patient_id = s.patient_id;

-- Payment ledger must be removed before its slots/structured bookings.
DELETE p
FROM payments p
JOIN delete_patient_ids d ON d.patient_id = p.patient_id;

DELETE b
FROM patient_slot_booking b
JOIN delete_patient_ids d ON d.patient_id = b.patient_id;

-- Delete mappings owned by these bookings only when no other child still uses
-- them. This removes the selected child's slot data without harming a shared
-- therapist slot.
DELETE t
FROM therapist_slot_mapping t
JOIN delete_therapist_slot_mapping_ids d ON d.mapping_id = t.id
LEFT JOIN patient_slot_booking remaining ON remaining.therapist_slot_mapping_id = t.id
WHERE remaining.id IS NULL;

DELETE c
FROM crt_program_bookings c
JOIN delete_patient_ids d ON d.patient_id = c.patient_id;

-- Session plans and items.
DELETE i
FROM patient_session_plan_item i
JOIN patient_session_plan p ON p.id = i.patient_session_plan_id
JOIN delete_patient_ids d ON d.patient_id = p.patient_id;

DELETE p
FROM patient_session_plan p
JOIN delete_patient_ids d ON d.patient_id = p.patient_id;

-- Remaining direct child records.
DELETE m
FROM parent_program_group_members m
JOIN delete_patient_ids d ON d.patient_id = m.patient_id;

DELETE x
FROM patient_program_pricing x
JOIN delete_patient_ids d ON d.patient_id = x.patient_id;

DELETE x
FROM patient_therapy x
JOIN delete_patient_ids d ON d.patient_id = x.patient_id;

DELETE x
FROM documents x
JOIN delete_patient_ids d ON d.patient_id = x.patient_id;

DELETE x
FROM patient_packages x
JOIN delete_patient_ids d ON d.patient_id = x.patient_id;

-- Delete the child master rows last.
DELETE p
FROM patients p
JOIN delete_patient_ids d ON d.patient_id = p.id;

-- Verification: this must return zero rows before committing.
SELECT p.id, p.first_name, p.last_name
FROM patients p
JOIN delete_patient_ids d ON d.patient_id = p.id;

-- SAFE DEFAULT: inspect results, then keep this ROLLBACK for a preview.
ROLLBACK;

-- For the permanent run, replace ROLLBACK above with:
-- COMMIT;

DROP TEMPORARY TABLE IF EXISTS delete_patient_ids;
DROP TEMPORARY TABLE IF EXISTS delete_therapist_slot_mapping_ids;
