"""Grant therapists view-only access to their dedicated payout page."""
from sqlalchemy import text

from app.core.database import engine


if __name__ == '__main__':
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO rbac_resources
                (code, resource_type, label, parent_code, display_order, is_active)
            VALUES
                ('menu.therapist_payouts', 'menu', 'Therapist Payouts', 'menu.reports', 51, 1)
            ON DUPLICATE KEY UPDATE
                label = VALUES(label), parent_code = VALUES(parent_code), is_active = 1
        """))
        connection.execute(text("""
            INSERT INTO rbac_role_permissions
                (role_id, resource_id, can_view, can_create, can_edit, can_delete)
            SELECT role.id, resource.id, 1, 0, 0, 0
            FROM roles role
            JOIN rbac_resources resource ON resource.code = 'menu.therapist_payouts'
            WHERE role.name = 'therapist' AND role.deleted_at IS NULL
            ON DUPLICATE KEY UPDATE can_view = 1, can_create = 0, can_edit = 0, can_delete = 0
        """))
        granted = connection.execute(text("""
            SELECT COUNT(*)
            FROM rbac_role_permissions permission
            JOIN roles role ON role.id = permission.role_id
            JOIN rbac_resources resource ON resource.id = permission.resource_id
            WHERE role.name = 'therapist'
              AND resource.code = 'menu.therapist_payouts'
              AND permission.can_view = 1
        """)).scalar()
    print(f'Therapist payout-only permission applied ({granted} role mapping).')
