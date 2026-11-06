"""Consolidate role aliases, preserving canonical IDs and user access."""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.core.database import SessionLocal

CANONICAL = {'admin', 'central_head', 'front_office', 'therapist'}
ALIASES = {'frontoffice': 'front_office', 'front_officer': 'front_office',
           'assessment_session': 'therapist', 'assessment_viewer_session': 'therapist'}


def main():
    with SessionLocal() as db:
        db.get_bind().echo = False
        roles = [dict(r._mapping) for r in db.execute(text('SELECT * FROM roles FOR UPDATE'))]
        assignments = [dict(r._mapping) for r in db.execute(text('SELECT * FROM user_roles FOR UPDATE'))]
        permissions = [dict(r._mapping) for r in db.execute(text('SELECT * FROM rbac_role_permissions FOR UPDATE'))]
        targets = {r['name']: r['id'] for r in roles if r['name'] in CANONICAL}
        if set(targets) != CANONICAL:
            raise RuntimeError('Missing canonical roles; no changes applied.')
        mapping = {}
        for role in roles:
            if role['name'] in CANONICAL:
                continue
            target = ALIASES.get(role['name'].lower())
            if not target:
                raise RuntimeError(f"Unknown role {role['name']}; no changes applied.")
            mapping[role['id']] = targets[target]
        backup = Path(__file__).resolve().parents[1] / 'tmp' / ('roles-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
        backup.parent.mkdir(exist_ok=True)
        backup.write_text(json.dumps({'roles': roles, 'user_roles': assignments, 'rbac_role_permissions': permissions}, default=str, indent=2))
        expected = {(a['user_id'], mapping.get(a['role_id'], a['role_id'])) for a in assignments if a['deleted_at'] is None}
        moved = 0
        for assignment in assignments:
            target = mapping.get(assignment['role_id'])
            if target is None:
                continue
            existing = db.execute(text('SELECT id,deleted_at FROM user_roles WHERE user_id=:user AND role_id=:role'), {'user': assignment['user_id'], 'role': target}).first()
            if existing:
                if assignment['deleted_at'] is None:
                    db.execute(text('UPDATE user_roles SET deleted_at=NULL WHERE id=:id'), {'id': existing.id})
                db.execute(text('DELETE FROM user_roles WHERE id=:id'), {'id': assignment['id']})
            else:
                db.execute(text('UPDATE user_roles SET role_id=:role WHERE id=:id'), {'role': target, 'id': assignment['id']})
            moved += 1
        for old in mapping:
            db.execute(text('DELETE FROM rbac_role_permissions WHERE role_id=:role'), {'role': old})
            db.execute(text('DELETE FROM roles WHERE id=:role'), {'role': old})
        actual = set(db.execute(text('SELECT user_id,role_id FROM user_roles WHERE deleted_at IS NULL')).all())
        if actual != expected or set(db.execute(text('SELECT name FROM roles')).scalars()) != CANONICAL:
            raise RuntimeError('Verification failed; transaction rolled back.')
        db.commit()
        print(f'Verified four canonical roles; migrated {moved} assignments. Backup: {backup}')
        print('Retained IDs:', targets)


if __name__ == '__main__':
    main()
