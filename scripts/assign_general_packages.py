"""Assign General packages to General, Social Group and Sushiksha Online at one centre."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func
from app.core.database import SessionLocal
from app.models.models import Package, PackageProgramAssignment, Program, Region


def assign(db, centre_name):
    centres = db.query(Region).filter(func.lower(Region.name) == centre_name.strip().lower(), Region.deleted_at.is_(None)).all()
    if len(centres) != 1:
        raise ValueError('Expected exactly one matching centre; no assignments changed.')
    centre = centres[0]
    names = ['general', 'social group', 'sushiksha online']
    programs = db.query(Program).filter(Program.region_id == centre.id, Program.is_active.is_(True),
        Program.deleted_at.is_(None), func.lower(Program.program_name).in_(names)).all()
    by_name = {program.program_name.lower(): program for program in programs}
    if set(by_name) != set(names):
        raise ValueError('General, Social Group and Sushiksha Online must all exist at this centre; no assignments changed.')
    packages = db.query(Package).filter(Package.region_id == centre.id,
        Package.program_id == by_name['general'].id, Package.is_active.is_(True),
        Package.deleted_at.is_(None)).with_for_update().all()
    if not packages:
        raise ValueError('No active General packages found; no assignments changed.')
    for package in packages:
        existing = {package.program_id} | {a.program_id for a in package.program_assignments}
        for program in programs:
            if program.id not in existing:
                package.program_assignments.append(PackageProgramAssignment(program_id=program.id))
    db.flush()
    return [(package.name, sorted({package.program_id} | {a.program_id for a in package.program_assignments})) for package in packages]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--centre', required=True)
    args = parser.parse_args()
    try:
        with SessionLocal.begin() as db:
            result = assign(db, args.centre)
        for name, program_ids in result:
            print(f'{name}: assigned at {args.centre}; program IDs {program_ids}')
    except Exception as exc:
        print(f'Assignment failed ({type(exc).__name__}); no transaction committed.', file=sys.stderr)
        sys.exit(1)
