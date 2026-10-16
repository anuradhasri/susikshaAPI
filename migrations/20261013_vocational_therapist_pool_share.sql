-- Vocational previously stored 50 in therapist_pool_percentage to represent
-- the fixed Sushiksha/therapist-pool split. The column now stores an
-- individual therapist's share of the 50% therapist pool, which defaults to 0.
UPDATE program_payment_config AS config
JOIN programs AS program ON program.id = config.program_id
SET config.therapist_pool_percentage = 0,
    config.updated_at = CURRENT_TIMESTAMP
WHERE LOWER(COALESCE(program.program_name, '')) LIKE '%vocational%'
  AND config.therapist_pool_percentage = 50;
