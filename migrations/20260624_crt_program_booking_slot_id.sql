ALTER TABLE crt_program_bookings
  ADD COLUMN slot_id INT NULL;

CREATE INDEX idx_crt_program_booking_session
  ON crt_program_bookings (patient_id, program_id, slot_date, slot_id);

UPDATE crt_program_bookings cpb
JOIN (
  SELECT
    psb.crt_program_booking_id,
    MIN(tsm.slot_id) AS slot_id
  FROM patient_slot_booking psb
  JOIN therapist_slot_mapping tsm ON tsm.id = psb.therapist_slot_mapping_id
  WHERE psb.crt_program_booking_id IS NOT NULL
  GROUP BY psb.crt_program_booking_id
) linked ON linked.crt_program_booking_id = cpb.id
SET cpb.slot_id = linked.slot_id
WHERE cpb.slot_id IS NULL;
