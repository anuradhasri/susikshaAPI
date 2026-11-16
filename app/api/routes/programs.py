from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes.ui import _require_user, _user_region_ids, _user_roles
from app.core.database import get_db
from app.models.models import Package, PackageProgramAssignment, Program, ProgramSegment, Region
from app.services.appointment_service import AppointmentService

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


class ProgramInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(ge=1, le=720)
    session_type: Literal['individual', 'group']
    capacity: int = Field(default=1, ge=1, le=100)
    session_amount: Price | None = None
    packages: list[PackageInput] | None = Field(default=None, max_length=30)


def centre_access(db, request, region_id):
    user = _require_user(request, db)
    roles = {role.lower().replace(' ', '_').replace('-', '_') for role in _user_roles(db, user)}
    ids = _user_region_ids(db, user)
    if not roles & STAFF or (region_id not in ids and (ids or not roles & GLOBAL)):
        raise HTTPException(403, 'You do not have access to manage programs at this centre.')
    if not db.query(Region.id).filter(Region.id == region_id, Region.deleted_at.is_(None)).first():
        raise HTTPException(404, 'Centre not found.')


def active_packages(db, region_id):
    return db.query(Package).filter(Package.region_id == region_id, Package.is_active.is_(True),
                                    Package.deleted_at.is_(None))


def shape(program, packages):
    package_only = AppointmentService._program_type(program.program_name) in {'crt', 'vocational'}
    return {'id': program.id, 'name': program.program_name, 'duration_minutes': program.duration_minutes,
            'session_type': 'group' if program.session_type == 'group' else 'individual',
            'capacity': program.capacity, 'session_amount': None if package_only else program.per_session_amount or None,
            'name_locked': program.program_name.lower() in BUILT_IN_NAMES,
            'packages': [{'id': row.id, 'sessions': row.total_sessions, 'amount': row.price,
                          'duration_days': row.duration_days} for row in packages]}


@router.get('/{region_id}')
def list_programs(region_id: int, request: Request, db: Session = Depends(get_db)):
    centre_access(db, request, region_id)
    packages_by_program = {}
    for row in active_packages(db, region_id).order_by(Package.total_sessions, Package.id).all():
        packages_by_program.setdefault(row.program_id, []).append(row)
    programs = db.query(Program).filter(Program.region_id == region_id, Program.is_active.is_(True),
                                        Program.deleted_at.is_(None)).order_by(Program.program_name).all()
    return {'data': [shape(program, packages_by_program.get(program.id, [])) for program in programs]}


@router.post('/{region_id}')
@router.put('/{region_id}/{program_id}')
def save_program(region_id: int, payload: ProgramInput, request: Request, program_id: int | None = None,
                 db: Session = Depends(get_db)):
    centre_access(db, request, region_id)
    name = payload.name.strip()
    if AppointmentService._program_type(name) in {'crt', 'vocational'}:
        if payload.session_amount is not None:
            raise HTTPException(422, 'Structured and Vocational programs require package pricing.')
    if not name:
        raise HTTPException(422, 'Enter a program name and configure session or package pricing.')
    if payload.session_type == 'group' and payload.capacity < 2:
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
    existing = {row.id: row for row in active_packages(db, region_id).filter_by(program_id=program_id).all()} if program_id else {}
    selected = [row.id for row in (payload.packages or []) if row.id is not None]
    if len(set(selected)) != len(selected) or not set(selected) <= existing.keys():
        raise HTTPException(422, 'Reload the program before editing its packages.')
    if program is None:
        program = Program(region_id=region_id, is_active=True)
        db.add(program)
    program.program_name = name
    program.duration_minutes = payload.duration_minutes
    program.session_type = payload.session_type
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
        for option in (payload.packages or []):
            previous = existing.pop(option.id, None)
            if previous and (previous.total_sessions, Decimal(str(previous.price)), previous.duration_days) == (
                option.sessions, option.amount, option.duration_days
            ):
                packages.append(previous)
                continue
            if previous:
                previous.is_active = False
            # Keep purchased packages linked to the terms they were sold under.
            row = Package(region_id=region_id, program_id=program.id, name=f'{name[:70]} {option.sessions} Session Package',
                          total_sessions=option.sessions, price=float(option.amount),
                          duration_days=option.duration_days, is_active=True)
            db.add(row)
            if previous:
                row.program_assignments = [PackageProgramAssignment(program_id=a.program_id) for a in previous.program_assignments]
            packages.append(row)
        if payload.packages is not None:
            for row in existing.values():
                row.is_active = False
        else:
            packages = list(existing.values())
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
    program_ids: list[int] = Field(min_length=1, max_length=100)


def assigned_ids(row):
    return sorted(set(([row.program_id] if row.program_id else []) + [a.program_id for a in row.program_assignments]))


@router.get('/{region_id}/packages')
def list_shared_packages(region_id: int, request: Request, db: Session = Depends(get_db)):
    centre_access(db, request, region_id)
    return {'data': [{'id': row.id, 'name': row.name, 'sessions': row.total_sessions,
                     'amount': row.price, 'duration_days': row.duration_days,
                     'program_ids': assigned_ids(row)}
                    for row in active_packages(db, region_id).order_by(Package.name, Package.id).all()]}


@router.post('/{region_id}/packages')
@router.put('/{region_id}/packages/{package_id}')
def save_shared_package(region_id: int, payload: SharedPackageInput, request: Request,
                        package_id: int | None = None, db: Session = Depends(get_db)):
    centre_access(db, request, region_id)
    ids = set(payload.program_ids)
    valid = db.query(Program).filter(Program.id.in_(ids), Program.region_id == region_id,
                                     Program.is_active.is_(True), Program.deleted_at.is_(None)).all()
    if len(valid) != len(ids) or any(AppointmentService._program_type(p.program_name) in {'parent_training', 'peg'} for p in valid):
        raise HTTPException(422, 'Select active programs from this centre. Manage parent programs separately.')
    row = active_packages(db, region_id).filter(Package.id == package_id).with_for_update().first() if package_id else None
    if package_id and row is None:
        raise HTTPException(404, 'Package not found. Reload the catalogue.')
    if row and (row.total_sessions, Decimal(str(row.price)), row.duration_days) != (payload.sessions, payload.amount, payload.duration_days):
        # Existing purchases retain the price, sessions and eligibility they were sold under.
        row.is_active = False
        row = None
    if row is None:
        row = Package(region_id=region_id, is_active=True)
        db.add(row)
    row.name = payload.name.strip()
    if not row.name:
        raise HTTPException(422, 'Enter a package name.')
    row.total_sessions = payload.sessions
    row.price = float(payload.amount)
    row.duration_days = payload.duration_days
    row.program_id = row.program_id if row.program_id in ids else min(ids)
    existing_assignments = {assignment.program_id: assignment for assignment in row.program_assignments}
    row.program_assignments = [existing_assignments.get(pid) or PackageProgramAssignment(program_id=pid)
                               for pid in sorted(ids - {row.program_id})]
    db.commit()
    return {'data': {'id': row.id}}
