from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes.ui import _require_user, _user_region_ids, _user_roles
from app.core.database import get_db
from app.models.models import Package, PackageProgramAssignment, PatientPackage, Program, ProgramSegment, Region
from app.services.appointment_service import AppointmentService
from app.services.package_catalogue import active_packages, lock_package_centre, validate_package_name

router = APIRouter(prefix='/api/v1/ui/program-catalog', tags=['program catalogue'])
STAFF = {'admin', 'front_office', 'frontoffice', 'front_officer', 'central_head', 'centralhead'}
GLOBAL = {'admin', 'central_head', 'centralhead'}
BUILT_IN_NAMES = {
    'general', 'crt', 'structured program', 'vocational', 'vocational program', 'social skills program',
    'schooling', 'parent training program: prewriting skills', 'parent training program: toilet training',
    'parent training program: peg',
}
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class PackageInput(BaseModel):
    id: int | None = None
    sessions: int = Field(ge=1, le=10000)
    amount: Price
    duration_days: int | None = Field(default=None, ge=1, le=3650)


class NewProgramPackage(PackageInput):
    name: str = Field(min_length=1, max_length=100)


class ProgramInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(ge=1, le=720)
    session_type: Literal['individual', 'group']
    capacity: int = Field(default=1, ge=0, le=100)
    session_amount: Price | None = None
    packages: list[PackageInput] | None = Field(default=None, max_length=30)
    package_ids: list[int] = Field(default_factory=list, max_length=100)
    new_packages: list[NewProgramPackage] = Field(default_factory=list, max_length=30)


def centre_access(db, request, region_id, *, lock_for_write=False):
    user = _require_user(request, db)
    roles = {role.lower().replace(' ', '_').replace('-', '_') for role in _user_roles(db, user)}
    ids = _user_region_ids(db, user)
    if not roles & STAFF or (region_id not in ids and (ids or not roles & GLOBAL)):
        raise HTTPException(403, 'You do not have access to manage programs at this centre.')
    centre = db.query(Region.id).filter(Region.id == region_id, Region.deleted_at.is_(None))
    if not (lock_package_centre(db, region_id) if lock_for_write else centre.first()):
        raise HTTPException(404, 'Centre not found.')


def is_package_priced(program):
    return not program.per_session_amount or AppointmentService._program_type(program.program_name) in {'crt', 'vocational'}


def validate_used_package_terms(row, sessions, amount):
    if (row.total_sessions, Decimal(str(row.price))) != (sessions, amount) and row.patient_packages:
        raise HTTPException(422, 'Price and session count are locked after a child has been assigned this package.')


def shape(program, packages):
    return {'id': program.id, 'name': program.program_name, 'duration_minutes': program.duration_minutes,
            'session_type': 'group' if program.session_type == 'group' else 'individual',
            'capacity': program.capacity, 'session_amount': None if is_package_priced(program) else program.per_session_amount,
            'name_locked': program.program_name.lower() in BUILT_IN_NAMES,
            'packages': [{'id': row.id, 'sessions': row.total_sessions, 'amount': row.price,
                          'duration_days': row.duration_days} for row in packages]}


@router.get('/{region_id}')
def list_programs(region_id: int, request: Request, db: Session = Depends(get_db)):
    centre_access(db, request, region_id)
    packages_by_program = {}
    for row in active_packages(db, region_id).order_by(Package.total_sessions, Package.id).all():
        for program_id in assigned_ids(row):
            packages_by_program.setdefault(program_id, []).append(row)
    programs = db.query(Program).filter(Program.region_id == region_id, Program.is_active.is_(True),
                                        Program.deleted_at.is_(None)).order_by(Program.program_name).all()
    return {'data': [shape(program, packages_by_program.get(program.id, [])) for program in programs]}


@router.post('/{region_id}')
@router.put('/{region_id}/{program_id}')
def save_program(region_id: int, payload: ProgramInput, request: Request, program_id: int | None = None,
                 db: Session = Depends(get_db)):
    centre_access(db, request, region_id, lock_for_write=True)
    name = payload.name.strip()
    if AppointmentService._program_type(name) in {'crt', 'vocational'}:
        if payload.session_amount is not None:
            raise HTTPException(422, 'Structured and Vocational programs require package pricing.')
    if not name:
        raise HTTPException(422, 'Enter a program name and configure session or package pricing.')
    option_count = len(payload.package_ids) + len(payload.new_packages) + len(payload.packages or [])
    if payload.session_amount is None and option_count != 1:
        raise HTTPException(422, 'Choose one existing package or create one new package.')
    if payload.session_amount is not None and option_count:
        raise HTTPException(422, 'Choose either per-session pricing or package pricing.')
    selected_packages = (
        active_packages(db, region_id).filter(Package.id.in_(payload.package_ids)).with_for_update().all()
        if payload.package_ids else []
    )
    if len(selected_packages) != len(set(payload.package_ids)):
        raise HTTPException(422, 'Choose active packages from this centre.')
    if payload.session_type == 'group' and payload.capacity == 1:
        raise HTTPException(422, 'A group session needs space for at least two children.')
    program = None
    if program_id:
        program = db.query(Program).filter_by(id=program_id, region_id=region_id, deleted_at=None).with_for_update().first()
        if not program:
            raise HTTPException(404, 'Program not found.')
        if program.program_name.lower() in BUILT_IN_NAMES and name != program.program_name:
            raise HTTPException(422, 'The name of this built-in program is used by its booking workflow.')
    duplicate = db.query(Program.id).filter(Program.region_id == region_id, func.lower(Program.program_name) == name.lower())
    if program_id:
        duplicate = duplicate.filter(Program.id != program_id)
    if duplicate.first():
        raise HTTPException(409, 'A program with that name already exists at this centre.')
    segments = [segment for segment in program.segments if segment.is_active] if program else []
    is_structured = AppointmentService._program_type(name) == 'crt'
    segment_count = len(segments) or (3 if is_structured else 0)
    if payload.duration_minutes < segment_count:
        raise HTTPException(422, 'Allow at least one minute per therapist segment.')
    existing = {row.id: row for row in AppointmentService._program_package_options(db, program).all()} if program else {}
    selected = [row.id for row in (payload.packages or []) if row.id is not None]
    if len(set(selected)) != len(selected) or not set(selected) <= existing.keys():
        raise HTTPException(422, 'Reload the program before editing its packages.')
    retained_ids = set(payload.package_ids) | set(selected)
    if program and (
        payload.session_amount is None or is_package_priced(program)
    ) and any(row.patient_packages for pid, row in existing.items() if pid not in retained_ids):
        raise HTTPException(422, 'This package is in use and cannot be removed from the program.')
    package_assignment_locked = bool(program and is_package_priced(program) and existing)
    if package_assignment_locked:
        if payload.session_amount is not None or not retained_ids or not retained_ids <= existing.keys():
            raise HTTPException(422, 'The assigned package for a package-based program is locked.')
        for option in payload.packages or []:
            current = existing[option.id]
            if (option.sessions, option.amount, option.duration_days) != (
                current.total_sessions, Decimal(str(current.price)), current.duration_days
            ):
                raise HTTPException(422, 'Edit package details in the package catalogue.')
    reused_packages = selected_packages + [existing[pid] for pid in selected]
    if not package_assignment_locked and any(set(assigned_ids(row)) - {program_id} for row in reused_packages):
        raise HTTPException(422, 'Choose an unassigned package or a package assigned only to this program.')

    if program is None:
        program = Program(region_id=region_id, is_active=True)
        db.add(program)
    program.program_name = name
    program.duration_minutes = payload.duration_minutes
    program.session_type = 'structured' if is_structured else payload.session_type
    program.capacity = payload.capacity if payload.session_type == 'group' else 1
    program.per_session_amount = float(payload.session_amount or 0)
    if segments and sum(segment.duration_minutes for segment in segments) != payload.duration_minutes:
        minutes, extra = divmod(payload.duration_minutes, len(segments))
        for index, segment in enumerate(segments):
            segment.duration_minutes = minutes + (index < extra)
    packages = []
    try:
        db.flush()
        if is_structured and not segments:
            minutes, extra = divmod(payload.duration_minutes, 3)
            for index in range(3):
                db.add(ProgramSegment(program_id=program.id, sequence_no=index + 1,
                                      label=f'Therapist {index + 1}', duration_minutes=minutes + (index < extra)))
        if package_assignment_locked:
            db.commit()
            return {'data': shape(program, list(existing.values()))}
        for option in (payload.packages or []):
            previous = existing.get(option.id)
            if previous:
                validate_used_package_terms(previous, option.sessions, option.amount)
                if previous.patient_packages:
                    previous.duration_days = option.duration_days
                    packages.append(previous)
                    continue
            if previous and (previous.total_sessions, Decimal(str(previous.price)), previous.duration_days) == (
                option.sessions, option.amount, option.duration_days
            ):
                packages.append(previous)
                continue
            # Keep purchased packages linked to the terms they were sold under.
            package_name = f'{name[:70]} {option.sessions} Session Package'
            replaced_id = previous.id if previous and assigned_ids(previous) == [program.id] else None
            validate_package_name(db, region_id, package_name, replaced_id)
            row = Package(region_id=region_id, program_id=program.id, name=package_name,
                          total_sessions=option.sessions, price=float(option.amount),
                          duration_days=option.duration_days, is_active=True)
            db.add(row)
            packages.append(row)
        for row in selected_packages:
            row.program_id = program.id
            row.program_assignments = []
            packages.append(row)
        for option in payload.new_packages:
            package_name = option.name.strip()
            validate_package_name(db, region_id, package_name)
            row = Package(region_id=region_id, program_id=program.id, name=package_name,
                          total_sessions=option.sessions, price=float(option.amount),
                          duration_days=option.duration_days, is_active=True)
            db.add(row)
            packages.append(row)
        if payload.session_amount is None:
            db.flush()
            for row in AppointmentService._program_package_options(db, program).filter(Package.id != packages[0].id).all():
                retire_program_assignment(db, row, program.id)
        else:
            packages = AppointmentService._program_package_options(db, program).all()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, 'A program with that name already exists at this centre.') from exc
    return {'data': shape(program, packages)}


class SharedPackageInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sessions: int = Field(ge=1, le=10000)
    amount: Price
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    program_ids: list[int] = Field(max_length=100)


def assigned_ids(row):
    return sorted(set(([row.program_id] if row.program_id else []) + [a.program_id for a in row.program_assignments]))


def retire_program_assignment(db, row, program_id):
    remaining_ids = set(assigned_ids(row)) - {program_id}
    row.is_active = False
    if remaining_ids:
        owner_id = row.program_id if row.program_id in remaining_ids else min(remaining_ids)
        replacement = Package(
            region_id=row.region_id, program_id=owner_id, name=row.name, description=row.description,
            total_sessions=row.total_sessions, price=row.price, duration_days=row.duration_days, is_active=True,
        )
        replacement.program_assignments = [
            PackageProgramAssignment(program_id=pid) for pid in sorted(remaining_ids - {owner_id})
        ]
        db.add(replacement)


@router.get('/{region_id}/packages')
def list_shared_packages(region_id: int, request: Request, db: Session = Depends(get_db)):
    centre_access(db, request, region_id)
    used = db.query(PatientPackage.id).filter(PatientPackage.package_id == Package.id).exists()
    rows = active_packages(db, region_id).add_columns(used).order_by(Package.name, Package.id).all()
    return {'data': [{'id': row.id, 'name': row.name, 'sessions': row.total_sessions,
                     'amount': row.price, 'duration_days': row.duration_days,
                     'program_ids': assigned_ids(row), 'terms_locked': bool(terms_locked)}
                    for row, terms_locked in rows]}


@router.post('/{region_id}/packages')
@router.put('/{region_id}/packages/{package_id}')
def save_shared_package(region_id: int, payload: SharedPackageInput, request: Request,
                        package_id: int | None = None, db: Session = Depends(get_db)):
    centre_access(db, request, region_id, lock_for_write=True)
    ids = set(payload.program_ids)
    valid = db.query(Program).filter(Program.id.in_(ids), Program.region_id == region_id,
                                     Program.is_active.is_(True), Program.deleted_at.is_(None)).all()
    if len(valid) != len(ids) or any(AppointmentService._program_type(p.program_name) in {'parent_training', 'peg'} for p in valid):
        raise HTTPException(422, 'Select active programs from this centre. Manage parent programs separately.')
    if len(ids) > 1 and any(is_package_priced(program) for program in valid):
        raise HTTPException(422, 'A package assigned to a package-based program cannot be shared with other programs.')

    row = active_packages(db, region_id).filter(Package.id == package_id).with_for_update().first() if package_id else None
    if package_id and row is None:
        raise HTTPException(404, 'Package not found. Reload the catalogue.')
    name = payload.name.strip()
    validate_package_name(db, region_id, name, package_id)
    for program in valid:
        if is_package_priced(program):
            options = AppointmentService._program_package_options(db, program)
            if row:
                options = options.filter(Package.id != row.id)
            if options.first():
                raise HTTPException(422, f'{program.program_name} already has a package.')

    description = row.description if row else None
    if row:
        validate_used_package_terms(row, payload.sessions, payload.amount)
        removed_ids = set(assigned_ids(row)) - ids
        if removed_ids and row.patient_packages:
            raise HTTPException(422, 'This package is in use and cannot be unassigned from its programs.')
        removed_programs = db.query(Program).filter(
            Program.id.in_(removed_ids), Program.region_id == region_id,
            Program.is_active.is_(True), Program.deleted_at.is_(None),
        ).all() if removed_ids else []
        if any(is_package_priced(program) for program in removed_programs):
            raise HTTPException(422, 'Package-based programs must keep a package. The existing assignment is locked.')
        terms_changed = (row.total_sessions, Decimal(str(row.price)), row.duration_days) != (
            payload.sessions, payload.amount, payload.duration_days
        )
        if (terms_changed and not row.patient_packages) or (removed_ids and not ids):
            # Keep previous purchase eligibility and prevent default packages from being seeded again.
            row.is_active = False
            row = None
    if row is None:
        row = Package(region_id=region_id, description=description, is_active=True)
        db.add(row)
    row.name = name
    row.total_sessions = payload.sessions
    row.price = float(payload.amount)
    row.duration_days = payload.duration_days
    row.program_id = row.program_id if row.program_id in ids else min(ids, default=None)
    existing_assignments = {assignment.program_id: assignment for assignment in row.program_assignments}
    row.program_assignments = [existing_assignments.get(pid) or PackageProgramAssignment(program_id=pid)
                               for pid in sorted(ids - {row.program_id})]
    db.commit()
    return {'data': {'id': row.id}}

