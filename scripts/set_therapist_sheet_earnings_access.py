"""Apply the therapist-only three-sheets/own-earnings permission policy."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.core.database import SessionLocal


def main():
    with SessionLocal() as db:
        db.get_bind().echo = False
        roles = db.execute(text("SELECT id,name FROM roles WHERE LOWER(name) IN ('therapist','assessment_session','assessment_viewer_session') AND deleted_at IS NULL")).all()
        if not roles:
            raise RuntimeError('Therapist role not found; no changes applied.')
        db.execute(text("""INSERT INTO rbac_resources (code,resource_type,label,parent_code,display_order,is_active)
            VALUES ('menu.appointments','menu','Appointments',NULL,10,1),
                   ('menu.my_earnings','menu','My Earnings',NULL,52,1),
                   ('menu.sheets','menu','Sheets',NULL,55,1),
                   ('report.action.create_sheet','action','Add report sheet','menu.sheets',56,1)
            ON DUPLICATE KEY UPDATE label=VALUES(label),is_active=1"""))
        for role_id, role_name in roles:
            db.execute(text('UPDATE rbac_role_permissions SET can_view=0,can_create=0,can_edit=0,can_delete=0 WHERE role_id=:role'), {'role': role_id})
            for code in ('menu.appointments', 'menu.sheets', 'menu.my_earnings', 'report.action.create_sheet'):
                db.execute(text('''INSERT INTO rbac_role_permissions (role_id,resource_id,can_view,can_create,can_edit,can_delete)
                    SELECT :role,id,1,:create,0,0 FROM rbac_resources WHERE code=:code
                    ON DUPLICATE KEY UPDATE can_view=1,can_create=VALUES(can_create),can_edit=0,can_delete=0'''),
                    {'role': role_id, 'code': code, 'create': int(code == 'report.action.create_sheet')})
            enabled = set(db.execute(text('''SELECT r.code FROM rbac_role_permissions p
                JOIN rbac_resources r ON r.id=p.resource_id WHERE p.role_id=:role AND p.can_view=1'''), {'role': role_id}).scalars())
            if enabled != {'menu.appointments', 'menu.sheets', 'menu.my_earnings', 'report.action.create_sheet'}:
                raise RuntimeError('Therapist permission verification failed; changes rolled back.')
            print(f'{role_name}: permission entries verified')
        db.commit()
        print('Therapist role variants: appointments, three sheets and own earnings only. Only RBAC permission records were changed.')


if __name__ == '__main__':
    main()
