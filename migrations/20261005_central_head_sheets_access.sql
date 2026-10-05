-- Grant Central Head the same Sheets view/create rights as therapist accounts.
INSERT INTO rbac_role_permissions (
    role_id, resource_id, can_view, can_create, can_edit, can_delete
)
SELECT role.id, resource.id, 1,
       CASE WHEN resource.code = 'report.action.create_sheet' THEN 1 ELSE 0 END,
       0, 0
FROM roles role
JOIN rbac_resources resource
  ON resource.code IN ('menu.sheets', 'report.action.create_sheet')
WHERE role.name = 'central_head'
  AND role.deleted_at IS NULL
ON DUPLICATE KEY UPDATE
    can_view = VALUES(can_view),
    can_create = VALUES(can_create),
    can_edit = VALUES(can_edit),
    can_delete = VALUES(can_delete);
