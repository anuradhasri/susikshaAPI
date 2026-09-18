# Child goal and observation sheets

Both sections appear under Reports. Each opens a paginated list before Add.
Observation records belong to a child and an observation date. They have no slot,
appointment, or session dependency. Saving completes a sheet; saved sheets open
read only. Multiple sheets, including sheets on the same date, are supported.
History is ordered by observation date descending, then sheet ID descending.

## Database installation

From `autism-backend`, run `python -m scripts.migrate_report_sheets` in the backend
environment. The additive migration creates the thirteen tables declared in
`app/models/report_sheets.py` and imports the goal catalog extracted from the
supplied Goal Sheet HTML. Existing records and master edits are preserved on
reruns. The initial catalog has 5 levels, 40 level-specific titles, 377 distinct
skills, 2,800 title/skill mappings, and 3 objective types.

The migration registers `report.action.create_sheet` and grants its create right
to existing admin/front-office roles that already have Reports access. Other
roles retain their existing access. Both Reports view and the new create right
are required to add a sheet. Child and therapist choices and record access are
restricted to the account's centres. Therapists must belong to the child's centre.

## Storage

- Masters: `goal_level_master`, `goal_title_master`, `goal_skill_master`,
  `goal_title_skill_mapping`, `goal_objective_type_master`.
- Goals: `patient_goal_sheet`, `patient_goal_sheet_therapist`, `patient_goal_sheet_item`,
  `patient_goal_sheet_item_skill`, `patient_goal_sheet_item_objective`.
- Observations: `patient_observation_sheet`,
  `patient_observation_sheet_therapist`, `patient_observation_sheet_entry`.

Observation entries retain their goal and objective text independently of later
master changes. NO/E/A labels follow the supplied HTML without guessing their
meaning. The observation objective dropdown loads distinct titles from the selected child's saved Goal Sheets. Selecting one fills the latest saved description for that title.
Planning and review months are stored as the first day of the respective month.

## Verification

Run `python -m unittest discover -s tests -p test_report_sheets.py` for isolated
persistence, hierarchy, permissions, date sorting, filters, pagination, and
validation tests. The tests never connect to the configured database.

For manual UI checks, `python -m scripts.preview_report_sheets` runs an isolated
in-memory API on `127.0.0.1:8013`. Start a separate Vite preview on port 5174 with
`VITE_API_BASE_URL=http://127.0.0.1:8013/api/v1`. Its sign-in uses the fictional
email `test@example.test` and any password. This helper is only for local testing;
do not use it as a deployed backend.

Goal sheets support multiple therapists. The additive migration backfills existing single-therapist sheets into the mapping table and preserves the original therapist column for compatibility. Listing filters match any selected therapist.

Planning and review now retain exact dates. Existing DATE columns and API field names planning_month/review_month are retained for compatibility. Earlier month-only records keep their stored first-of-month dates. Listing ranges use full dates; the month filter remains available.
