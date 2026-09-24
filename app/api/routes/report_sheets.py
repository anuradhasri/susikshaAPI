from datetime import date
from calendar import monthrange
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, func, String
from sqlalchemy.orm import Session, selectinload, joinedload
from app.core.database import get_db
from app.models.models import Patient, Therapist
from app.models.report_sheets import (
    GoalLevelMaster, GoalTitleMaster, GoalDomainMaster, GoalSkillMaster, GoalTitleSkillMapping,
    GoalObjectiveTypeMaster, PatientGoalSheet, PatientGoalSheetTherapist, PatientGoalSheetItem,
    PatientGoalSheetItemDomain, PatientGoalSheetItemSkill, PatientGoalSheetItemObjective,
    PatientObservationSheet, PatientObservationSheetTherapist, PatientObservationSheetEntry,
    PatientObservationSheetEntryDomain, PatientObservationSheetEntrySkill,
    PatientGoalSummary, PatientGoalSummaryTherapist, PatientGoalSummaryResponse,
)
from app.api.routes.ui import _require_user, _permission_shape, _user_region_ids, _user_roles

router = APIRouter(prefix='/api/v1/ui/reports/sheets', tags=['report-sheets'])
PROMPTS = ['', 'Modelling', 'Gestural Prompt', 'Full Physical Prompt', 'Partial Physical Prompt', 'Visual Prompt']


class ObjectiveInput(BaseModel):
    type_id: int
    text: str = Field(default='', max_length=10000)


class GoalInput(BaseModel):
    level_id: Optional[int] = None
    title_id: Optional[int] = None
    description: str = Field(min_length=1, max_length=20000)
    domain_ids: list[int] = Field(default_factory=list, max_length=100)
    skill_ids: list[int] = Field(default_factory=list, max_length=100)
    objectives: list[ObjectiveInput] = Field(default_factory=list, max_length=20)


class GoalSheetInput(BaseModel):
    patient_id: int
    planning_month: date
    review_month: date
    therapist_id: Optional[int] = None
    therapist_ids: Optional[list[int]] = Field(default=None, min_length=1, max_length=50)
    parent_name: str = Field(default='', max_length=255)
    parental_objectives: str = Field(default='', max_length=20000)
    items: list[GoalInput] = Field(min_length=1, max_length=100)


class ObservationEntryInput(BaseModel):
    level_id: Optional[int] = None
    title_id: Optional[int] = None
    domain_ids: list[int] = Field(default_factory=list, max_length=100)
    skill_ids: list[int] = Field(default_factory=list, max_length=200)
    objective: str = Field(default='', max_length=255)
    goal: str = Field(min_length=1, max_length=20000)
    activities: str = Field(default='', max_length=20000)
    accuracy: Optional[float] = Field(default=None, ge=0, le=100)
    accuracy_status: Literal['NO', 'E', 'A'] = 'NO'
    prompts: str = Field(default='', max_length=100)
    responses: str = Field(default='', max_length=20000)
    concerns: str = Field(default='', max_length=20000)
    strategy: str = Field(default='', max_length=20000)


class ObservationSheetInput(BaseModel):
    patient_id: int
    observation_date: date
    therapist_ids: list[int] = Field(min_length=1, max_length=50)
    entries: list[ObservationEntryInput] = Field(min_length=1, max_length=100)


class GoalSummaryResponseInput(BaseModel):
    skill_id: int
    status: Literal['NO', 'E', 'A'] = 'NO'
    comments: str = Field(default='', max_length=2000)


class GoalSummaryInput(BaseModel):
    patient_id: int
    evaluation_date: date
    re_evaluation_date: Optional[date] = None
    therapist_ids: list[int] = Field(min_length=1, max_length=50)
    informant: str = Field(default='', max_length=255)
    level_id: int
    review_date: Optional[date] = None
    responses: list[GoalSummaryResponseInput] = Field(min_length=1, max_length=500)


def authorize(request, db, action='view'):
    user = _require_user(request, db)
    permissions = _permission_shape(db, user)
    if not permissions.get('menu.reports', {}).get('view', False) or (action == 'create' and not permissions.get('report.action.create_sheet', {}).get('create', False)):
        raise HTTPException(403, 'You do not have permission to access these reports.')
    return user


def region_ids(db, user):
    ids = _user_region_ids(db, user)
    if not ids and not (_user_roles(db, user) & {'admin', 'central_head'}):
        raise HTTPException(403, 'No centre is assigned to your account.')
    return ids


def patient_for(db, user, patient_id):
    query = db.query(Patient).filter(Patient.id == patient_id)
    ids = region_ids(db, user)
    if ids:
        query = query.filter(Patient.region_id.in_(ids))
    patient = query.first()
    if not patient:
        raise HTTPException(404, 'Child not found.')
    return patient


def validate_therapists(db, ids, region_id):
    unique_ids = set(ids)
    rows = db.query(Therapist).filter(Therapist.id.in_(unique_ids), Therapist.is_active.is_(True), Therapist.region_id == region_id).all()
    if len(rows) != len(unique_ids):
        raise HTTPException(422, 'Select active therapists belonging to the child’s centre.')
    return sorted(unique_ids)


def validate_catalog_selection(db, level_id, title_id, domain_ids, skill_ids):
    domains = set(domain_ids)
    skills = set(skill_ids)
    title = db.get(GoalTitleMaster, title_id) if title_id else None
    if not level_id and title:
        level_id = title.level_id
    skill_rows = db.query(GoalSkillMaster).filter(GoalSkillMaster.id.in_(skills)).all() if skills else []
    if skills and not domains:
        domains = {row.domain_id for row in skill_rows if row.domain_id is not None}
    if not level_id:
        if title_id or domains or skills:
            raise HTTPException(422, 'Select a level before selecting a Common Goal, domain, or skill.')
        return None, []
    if not db.get(GoalLevelMaster, level_id):
        raise HTTPException(422, 'Invalid level.')
    if title_id and (not title or title.level_id != level_id):
        raise HTTPException(422, 'Select a Common Goal belonging to the selected level.')
    valid_domains = {row.id for row in db.query(GoalDomainMaster.id).filter(GoalDomainMaster.level_id == level_id, GoalDomainMaster.id.in_(domains)).all()} if domains else set()
    if valid_domains != domains:
        raise HTTPException(422, 'Selected domains must belong to the selected level.')
    if skills and not domains:
        legacy_linked = {row.skill_id for row in db.query(GoalTitleSkillMapping.skill_id).filter(GoalTitleSkillMapping.title_id == title_id, GoalTitleSkillMapping.skill_id.in_(skills)).all()} if title_id else set()
        if legacy_linked != skills:
            raise HTTPException(422, 'Selected skills are not assigned to a domain.')
        return level_id, []
    valid_skills = {row.id for row in db.query(GoalSkillMaster.id).filter(GoalSkillMaster.level_id == level_id, GoalSkillMaster.domain_id.in_(domains), GoalSkillMaster.id.in_(skills)).all()} if skills else set()
    if valid_skills != skills:
        raise HTTPException(422, 'Selected skills must belong to the selected level and domains.')
    return level_id, sorted(domains)


def model_for(kind):
    return PatientGoalSheet if kind == 'goals' else PatientObservationSheet


def query_for(db, user, kind):
    model = model_for(kind)
    query = db.query(model)
    ids = region_ids(db, user)
    if ids:
        query = query.filter(model.region_id.in_(ids))
    if kind == 'goals':
        return query.options(selectinload(model.patient), selectinload(model.region), selectinload(model.therapist), selectinload(model.therapists).selectinload(PatientGoalSheetTherapist.therapist), selectinload(model.items).selectinload(PatientGoalSheetItem.domains), selectinload(model.items).selectinload(PatientGoalSheetItem.skills), selectinload(model.items).selectinload(PatientGoalSheetItem.objectives))
    return query.options(selectinload(model.patient), selectinload(model.region), selectinload(model.therapists).selectinload(PatientObservationSheetTherapist.therapist), selectinload(model.entries).selectinload(PatientObservationSheetEntry.domains), selectinload(model.entries).selectinload(PatientObservationSheetEntry.skills))


def shape(row, kind, detail=False):
    result = {'id': row.id, 'patient_id': row.patient_id,
              'patient_name': f'{row.patient.first_name} {row.patient.last_name}'.strip(),
              'region_id': row.region_id, 'region_name': row.region.name,
              'created_at': row.created_at, 'updated_at': row.updated_at,
              'can_edit': bool(row.created_at and row.created_at.date() == date.today())}
    if kind == 'goals':
        therapists = sorted([t.therapist for t in row.therapists] or [row.therapist], key=lambda t: (t.name.casefold(), t.id))
        result.update(planning_month=row.planning_month, review_month=row.review_month,
                      therapist_id=row.therapist_id, therapist_ids=[t.id for t in therapists], therapist_names=[t.name for t in therapists],
                      item_count=len(row.items))
        if detail:
            result.update(parent_name=row.parent_name, parental_objectives=row.parental_objectives,
                          items=[{'id': i.id, 'level_id': i.level_id, 'title_id': i.title_id,
                                  'description': i.description, 'domain_ids': [d.domain_id for d in i.domains], 'skill_ids': [s.skill_id for s in i.skills],
                                  'objectives': [{'type_id': o.type_id, 'text': o.text} for o in i.objectives]} for i in row.items])
    else:
        result.update(observation_date=row.observation_date,
                      therapist_ids=[t.therapist_id for t in row.therapists],
                      therapist_names=[t.therapist.name for t in row.therapists], item_count=len(row.entries))
        if detail:
            fields = ['id', 'level_id', 'title_id', 'objective', 'goal', 'activities', 'accuracy', 'accuracy_status', 'prompts', 'responses', 'concerns', 'strategy']
            result['entries'] = [{**{key: getattr(e, key) for key in fields}, 'domain_ids': [d.domain_id for d in e.domains], 'skill_ids': [s.skill_id for s in e.skills]} for e in row.entries]
    return result


@router.get('/masters')
def masters(request: Request, region_id: Optional[int] = None, db: Session = Depends(get_db)):
    user = authorize(request, db)
    ids = region_ids(db, user)
    patients = db.query(Patient).options(joinedload(Patient.region))
    therapists = db.query(Therapist).filter(Therapist.is_active.is_(True))
    if ids:
        patients = patients.filter(Patient.region_id.in_(ids))
        therapists = therapists.filter(Therapist.region_id.in_(ids))
    if region_id:
        patients = patients.filter(Patient.region_id == region_id)
        therapists = therapists.filter(Therapist.region_id == region_id)
    return {'data': {
        'patients': [{'id': p.id, 'name': f'{p.first_name} {p.last_name}'.strip(), 'date_of_birth': p.date_of_birth,
                      'region_id': p.region_id,
                      'region_name': p.region.name, 'parent_name': p.father_name or p.mother_name or ''} for p in patients.order_by(Patient.first_name, Patient.id).all()],
        'therapists': [{'id': t.id, 'name': t.name, 'region_id': t.region_id} for t in therapists.order_by(Therapist.name).all()],
        'levels': [{'id': l.id, 'name': l.name} for l in db.query(GoalLevelMaster).order_by(GoalLevelMaster.sort_order).all()],
        'titles': [{'id': t.id, 'level_id': t.level_id, 'title': t.title, 'description': t.description} for t in db.query(GoalTitleMaster).order_by(GoalTitleMaster.id).all()],
        'domains': [{'id': d.id, 'level_id': d.level_id, 'code': d.code, 'description': d.description} for d in db.query(GoalDomainMaster).order_by(GoalDomainMaster.level_id, GoalDomainMaster.sort_order, GoalDomainMaster.id).all()],
        'skills': [{'id': s.id, 'level_id': s.level_id, 'domain_id': s.domain_id, 'code': s.code, 'description': s.description} for s in db.query(GoalSkillMaster).order_by(GoalSkillMaster.level_id, GoalSkillMaster.id).all()],
        'title_skills': [{'title_id': m.title_id, 'skill_id': m.skill_id} for m in db.query(GoalTitleSkillMapping).all()],
        'objective_types': [{'id': t.id, 'code': t.code, 'name': t.name} for t in db.query(GoalObjectiveTypeMaster).order_by(GoalObjectiveTypeMaster.id).all()],
        'prompts': PROMPTS[1:],
    }}


@router.get('/observations/goal-titles/{patient_id}')
def child_goal_titles(patient_id: int, request: Request, db: Session = Depends(get_db)):
    user = authorize(request, db)
    patient_for(db, user, patient_id)
    rows = db.query(PatientGoalSheetItem, GoalTitleMaster).join(
        PatientGoalSheet, PatientGoalSheet.id == PatientGoalSheetItem.sheet_id
    ).join(GoalTitleMaster, GoalTitleMaster.id == PatientGoalSheetItem.title_id).filter(
        PatientGoalSheet.patient_id == patient_id
    ).order_by(PatientGoalSheet.planning_month.desc(), PatientGoalSheet.id.desc(), PatientGoalSheetItem.sort_order).all()
    titles = {}
    for item, title in rows:
        if title.id not in titles:
            titles[title.id] = {'id': title.id, 'level_id': item.level_id or title.level_id,
                                'title': title.title, 'description': item.description}
    return {'data': sorted(titles.values(), key=lambda t: (t['title'].casefold(), t['id']))}


@router.get('/observations/history/{patient_id}')
def history(patient_id: int, request: Request, db: Session = Depends(get_db)):
    user = authorize(request, db)
    patient_for(db, user, patient_id)
    rows = db.query(PatientObservationSheet).options(selectinload(PatientObservationSheet.therapists).selectinload(PatientObservationSheetTherapist.therapist)).filter(PatientObservationSheet.patient_id == patient_id).order_by(PatientObservationSheet.observation_date.desc(), PatientObservationSheet.id.desc()).all()
    return {'data': [{'id': r.id, 'observation_date': r.observation_date,
                      'therapist_names': [t.therapist.name for t in r.therapists]} for r in rows]}


def goal_summary_query(db, user):
    query = db.query(PatientGoalSummary).options(
        selectinload(PatientGoalSummary.patient), selectinload(PatientGoalSummary.region),
        selectinload(PatientGoalSummary.therapist), selectinload(PatientGoalSummary.therapists).selectinload(PatientGoalSummaryTherapist.therapist), selectinload(PatientGoalSummary.level),
        selectinload(PatientGoalSummary.responses),
    )
    ids = region_ids(db, user)
    return query.filter(PatientGoalSummary.region_id.in_(ids)) if ids else query


def shape_goal_summary(row, detail=False):
    therapist_rows = row.therapists or []
    therapist_ids = [item.therapist_id for item in therapist_rows] or [row.therapist_id]
    therapist_names = [item.therapist.name for item in therapist_rows] or [row.therapist.name]
    result = {
        'id': row.id, 'patient_id': row.patient_id,
        'patient_name': f'{row.patient.first_name} {row.patient.last_name}'.strip(),
        'date_of_birth': row.patient.date_of_birth, 'region_id': row.region_id,
        'region_name': row.region.name, 'evaluation_date': row.evaluation_date,
        're_evaluation_date': row.re_evaluation_date, 'therapist_id': row.therapist_id,
        'therapist_id': therapist_ids[0], 'therapist_name': ', '.join(therapist_names),
        'therapist_ids': therapist_ids, 'therapist_names': therapist_names, 'informant': row.informant,
        'level_id': row.level_id, 'level_name': row.level.name,
        'review_date': row.review_date, 'response_count': len(row.responses),
        'created_at': row.created_at, 'updated_at': row.updated_at,
    }
    if detail:
        result['responses'] = [{'skill_id': response.skill_id, 'status': response.status,
                                'comments': response.comments} for response in row.responses]
    return result


def validate_goal_summary(db, payload):
    level = db.get(GoalLevelMaster, payload.level_id)
    if not level:
        raise HTTPException(422, 'Select a valid Sushiksha level.')
    skill_ids = [response.skill_id for response in payload.responses]
    if len(skill_ids) != len(set(skill_ids)):
        raise HTTPException(422, 'Each checklist question can only be answered once.')
    valid_ids = {row.id for row in db.query(GoalSkillMaster.id).filter(
        GoalSkillMaster.level_id == payload.level_id, GoalSkillMaster.id.in_(skill_ids)).all()}
    if valid_ids != set(skill_ids):
        raise HTTPException(422, 'Checklist questions must belong to the selected level.')


@router.get('/summaries')
def goal_summary_listing(request: Request, patient_id: Optional[int] = None,
                         therapist_id: Optional[int] = None, region_id: Optional[int] = None,
                         start_date: Optional[date] = None, end_date: Optional[date] = None,
                         search: str = Query('', max_length=255),
                         page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100),
                         db: Session = Depends(get_db)):
    user = authorize(request, db)
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, 'From date must be before or equal to To date.')
    query = goal_summary_query(db, user)
    if patient_id:
        query = query.filter(PatientGoalSummary.patient_id == patient_id)
    if therapist_id:
        query = query.filter(or_(PatientGoalSummary.therapist_id == therapist_id,
            PatientGoalSummary.therapists.any(PatientGoalSummaryTherapist.therapist_id == therapist_id)))
    if region_id:
        query = query.filter(PatientGoalSummary.region_id == region_id)
    if start_date:
        query = query.filter(PatientGoalSummary.evaluation_date >= start_date)
    if end_date:
        query = query.filter(PatientGoalSummary.evaluation_date <= end_date)
    if search.strip():
        name = func.concat(Patient.first_name, ' ', Patient.last_name)
        query = query.join(Patient, Patient.id == PatientGoalSummary.patient_id).filter(
            or_(name.ilike(f'%{search.strip()}%'), func.cast(Patient.id, String).ilike(f'%{search.strip()}%')))
    total = query.count()
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    rows = query.order_by(PatientGoalSummary.evaluation_date.desc(), PatientGoalSummary.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {'data': [shape_goal_summary(row) for row in rows], 'total': total, 'page': page,
            'page_size': page_size, 'pages': pages}


@router.get('/summaries/{summary_id}')
def goal_summary_detail(summary_id: int, request: Request, db: Session = Depends(get_db)):
    user = authorize(request, db)
    row = goal_summary_query(db, user).filter(PatientGoalSummary.id == summary_id).first()
    if not row:
        raise HTTPException(404, 'Goal summary not found.')
    return {'data': shape_goal_summary(row, True)}


@router.post('/summaries', status_code=201)
def create_goal_summary(payload: GoalSummaryInput, request: Request, db: Session = Depends(get_db)):
    user = authorize(request, db, 'create')
    patient = patient_for(db, user, payload.patient_id)
    validate_therapists(db, payload.therapist_ids, patient.region_id)
    validate_goal_summary(db, payload)
    row = PatientGoalSummary(patient_id=patient.id, region_id=patient.region_id,
        evaluation_date=payload.evaluation_date, re_evaluation_date=payload.re_evaluation_date,
        therapist_id=payload.therapist_ids[0], informant=payload.informant.strip(),
        level_id=payload.level_id, review_date=payload.review_date,
        created_by=user.id, updated_by=user.id)
    row.responses = [PatientGoalSummaryResponse(skill_id=item.skill_id, status=item.status,
        comments=item.comments.strip()) for item in payload.responses]
    row.therapists = [PatientGoalSummaryTherapist(therapist_id=therapist_id) for therapist_id in payload.therapist_ids]
    db.add(row)
    db.commit()
    return {'data': shape_goal_summary(row, True)}


@router.put('/summaries/{summary_id}')
def update_goal_summary(summary_id: int, payload: GoalSummaryInput, request: Request, db: Session = Depends(get_db)):
    user = authorize(request, db, 'create')
    row = goal_summary_query(db, user).filter(PatientGoalSummary.id == summary_id).first()
    if not row:
        raise HTTPException(404, 'Goal summary not found.')
    if payload.patient_id != row.patient_id:
        raise HTTPException(422, 'The child cannot be changed after saving the summary.')
    validate_therapists(db, payload.therapist_ids, row.region_id)
    validate_goal_summary(db, payload)
    row.evaluation_date = payload.evaluation_date
    row.re_evaluation_date = payload.re_evaluation_date
    row.therapist_id = payload.therapist_ids[0]
    row.therapists = [PatientGoalSummaryTherapist(therapist_id=therapist_id) for therapist_id in payload.therapist_ids]
    row.informant = payload.informant.strip()
    row.level_id = payload.level_id
    row.review_date = payload.review_date
    row.updated_by = user.id
    # Replace only the answers for the level being updated. Answers recorded for
    # other levels belong to the same summary and must survive level switching.
    submitted_level_skill_ids = {skill_id for (skill_id,) in db.query(GoalSkillMaster.id).filter(
        GoalSkillMaster.level_id == payload.level_id).all()}
    row.responses[:] = [response for response in row.responses
                        if response.skill_id not in submitted_level_skill_ids]
    db.flush()
    row.responses.extend(PatientGoalSummaryResponse(skill_id=item.skill_id, status=item.status,
        comments=item.comments.strip()) for item in payload.responses)
    db.commit()
    return {'data': shape_goal_summary(row, True)}


@router.get('/{kind}')
def listing(kind: Literal['goals', 'observations'], request: Request,
            patient_id: Optional[int] = None, therapist_id: Optional[int] = None,
            region_id: Optional[int] = None, start_date: Optional[date] = None,
            end_date: Optional[date] = None, search: str = Query('', max_length=255),
            month: Optional[str] = Query(None, pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
            page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100),
            db: Session = Depends(get_db)):
    user = authorize(request, db)
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, 'From date must be before or equal to To date.')
    model = model_for(kind)
    date_column = model.planning_month if kind == 'goals' else model.observation_date
    query = query_for(db, user, kind)
    if month:
        year_number, month_number = map(int, month.split('-'))
        try:
            month_start = date(year_number, month_number, 1)
            month_end = date(year_number, month_number, monthrange(year_number, month_number)[1])
        except ValueError:
            raise HTTPException(422, 'Select a valid month.')
        query = query.filter(date_column >= month_start, date_column <= month_end)
    if patient_id:
        query = query.filter(model.patient_id == patient_id)
    if region_id:
        query = query.filter(model.region_id == region_id)
    if start_date:
        query = query.filter(date_column >= start_date)
    if end_date:
        query = query.filter(date_column <= end_date)
    if therapist_id:
        query = query.filter(or_(model.therapist_id == therapist_id, model.therapists.any(PatientGoalSheetTherapist.therapist_id == therapist_id))) if kind == 'goals' else query.filter(model.therapists.any(PatientObservationSheetTherapist.therapist_id == therapist_id))
    if search.strip():
        name = func.concat(Patient.first_name, ' ', Patient.last_name)
        query = query.join(Patient, Patient.id == model.patient_id).filter(or_(name.ilike(f'%{search.strip()}%'), func.cast(Patient.id, String).ilike(f'%{search.strip()}%')))
    total = query.count()
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    rows = query.order_by(date_column.desc(), model.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {'data': [shape(r, kind) for r in rows], 'total': total, 'page': page, 'page_size': page_size, 'pages': pages}


@router.get('/{kind}/{sheet_id}')
def detail(kind: Literal['goals', 'observations'], sheet_id: int, request: Request, db: Session = Depends(get_db)):
    user = authorize(request, db)
    row = query_for(db, user, kind).filter(model_for(kind).id == sheet_id).first()
    if not row:
        raise HTTPException(404, 'Sheet not found.')
    return {'data': shape(row, kind, True)}


@router.post('/goals', status_code=201)
def create_goal(payload: GoalSheetInput, request: Request, db: Session = Depends(get_db)):
    user = authorize(request, db, 'create')
    patient = patient_for(db, user, payload.patient_id)
    selected_ids = payload.therapist_ids if payload.therapist_ids is not None else ([payload.therapist_id] if payload.therapist_id is not None else [])
    if not selected_ids:
        raise HTTPException(422, 'Select at least one therapist.')
    ids = validate_therapists(db, selected_ids, patient.region_id)
    planning = payload.planning_month
    review = payload.review_month
    if review < planning:
        raise HTTPException(422, 'Review date cannot be before the planning date.')
    row = PatientGoalSheet(patient_id=patient.id, region_id=patient.region_id,
                           planning_month=planning, review_month=review,
                           therapist_id=ids[0], parent_name=payload.parent_name.strip(),
                           parental_objectives=payload.parental_objectives, created_by=user.id, updated_by=user.id)
    row.therapists = [PatientGoalSheetTherapist(therapist_id=t) for t in ids]
    valid_types = {t.id for t in db.query(GoalObjectiveTypeMaster).all()}
    for index, item in enumerate(payload.items):
        if not item.description.strip():
            raise HTTPException(422, 'Each goal needs a description.')
        normalized_level_id, normalized_domain_ids = validate_catalog_selection(db, item.level_id, item.title_id, item.domain_ids, item.skill_ids)
        if any(o.type_id not in valid_types for o in item.objectives) or len({o.type_id for o in item.objectives}) != len(item.objectives):
            raise HTTPException(422, 'Select distinct valid objective types.')
        child = PatientGoalSheetItem(level_id=normalized_level_id, title_id=item.title_id, description=item.description.strip(), sort_order=index)
        child.domains = [PatientGoalSheetItemDomain(domain_id=d) for d in normalized_domain_ids]
        child.skills = [PatientGoalSheetItemSkill(skill_id=s) for s in sorted(set(item.skill_ids))]
        child.objectives = [PatientGoalSheetItemObjective(type_id=o.type_id, text=o.text) for o in item.objectives]
        row.items.append(child)
    db.add(row)
    db.commit()
    return {'data': shape(row, 'goals', True)}


@router.post('/observations', status_code=201)
def create_observation(payload: ObservationSheetInput, request: Request, db: Session = Depends(get_db)):
    user = authorize(request, db, 'create')
    patient = patient_for(db, user, payload.patient_id)
    ids = validate_therapists(db, payload.therapist_ids, patient.region_id)
    row = PatientObservationSheet(patient_id=patient.id, region_id=patient.region_id,
                                  observation_date=payload.observation_date, created_by=user.id, updated_by=user.id)
    row.therapists = [PatientObservationSheetTherapist(therapist_id=t) for t in ids]
    for index, entry in enumerate(payload.entries):
        if not entry.goal.strip():
            raise HTTPException(422, 'Each observation entry needs a goal.')
        if entry.prompts not in PROMPTS:
            raise HTTPException(422, 'Select a valid prompt.')
        normalized_level_id, normalized_domain_ids = validate_catalog_selection(db, entry.level_id, entry.title_id, entry.domain_ids, entry.skill_ids)
        title = db.get(GoalTitleMaster, entry.title_id) if entry.title_id else None
        values = entry.model_dump()
        domain_ids = values.pop('domain_ids')
        skill_ids = values.pop('skill_ids')
        values['level_id'] = normalized_level_id
        if title:
            values['objective'] = title.title
        values['goal'] = entry.goal.strip()
        child = PatientObservationSheetEntry(**values, sort_order=index)
        child.domains = [PatientObservationSheetEntryDomain(domain_id=d) for d in normalized_domain_ids]
        child.skills = [PatientObservationSheetEntrySkill(skill_id=s) for s in sorted(set(skill_ids))]
        row.entries.append(child)
    db.add(row)
    db.commit()
    return {'data': shape(row, 'observations', True)}


def require_same_day_edit(row):
    if not row.created_at or row.created_at.date() != date.today():
        raise HTTPException(403, 'This sheet can only be edited on the day it was created.')


@router.put('/goals/{sheet_id}')
def update_goal(sheet_id: int, payload: GoalSheetInput, request: Request, db: Session = Depends(get_db)):
    user = authorize(request, db, 'create')
    row = query_for(db, user, 'goals').filter(PatientGoalSheet.id == sheet_id).first()
    if not row:
        raise HTTPException(404, 'Sheet not found.')
    require_same_day_edit(row)
    if payload.patient_id != row.patient_id:
        raise HTTPException(422, 'The child cannot be changed after saving the sheet.')
    ids = validate_therapists(db, payload.therapist_ids or ([payload.therapist_id] if payload.therapist_id else []), row.region_id)
    if payload.review_month < payload.planning_month:
        raise HTTPException(422, 'Review date cannot be before the planning date.')
    row.planning_month = payload.planning_month
    row.review_month = payload.review_month
    row.therapist_id = ids[0]
    row.parent_name = payload.parent_name.strip()
    row.parental_objectives = payload.parental_objectives
    row.updated_by = user.id
    row.therapists.clear()
    row.items.clear()
    db.flush()
    row.therapists = [PatientGoalSheetTherapist(therapist_id=t) for t in ids]
    valid_types = {t.id for t in db.query(GoalObjectiveTypeMaster).all()}
    for index, item in enumerate(payload.items):
        if not item.description.strip():
            raise HTTPException(422, 'Each goal needs a description.')
        normalized_level_id, normalized_domain_ids = validate_catalog_selection(db, item.level_id, item.title_id, item.domain_ids, item.skill_ids)
        if any(o.type_id not in valid_types for o in item.objectives) or len({o.type_id for o in item.objectives}) != len(item.objectives):
            raise HTTPException(422, 'Select distinct valid objective types.')
        child = PatientGoalSheetItem(level_id=normalized_level_id, title_id=item.title_id, description=item.description.strip(), sort_order=index)
        child.domains = [PatientGoalSheetItemDomain(domain_id=d) for d in normalized_domain_ids]
        child.skills = [PatientGoalSheetItemSkill(skill_id=s) for s in sorted(set(item.skill_ids))]
        child.objectives = [PatientGoalSheetItemObjective(type_id=o.type_id, text=o.text) for o in item.objectives]
        row.items.append(child)
    db.commit()
    return {'data': shape(row, 'goals', True)}


@router.put('/observations/{sheet_id}')
def update_observation(sheet_id: int, payload: ObservationSheetInput, request: Request, db: Session = Depends(get_db)):
    user = authorize(request, db, 'create')
    row = query_for(db, user, 'observations').filter(PatientObservationSheet.id == sheet_id).first()
    if not row:
        raise HTTPException(404, 'Sheet not found.')
    require_same_day_edit(row)
    if payload.patient_id != row.patient_id:
        raise HTTPException(422, 'The child cannot be changed after saving the sheet.')
    ids = validate_therapists(db, payload.therapist_ids, row.region_id)
    row.observation_date = payload.observation_date
    row.updated_by = user.id
    row.therapists.clear()
    row.entries.clear()
    db.flush()
    row.therapists = [PatientObservationSheetTherapist(therapist_id=t) for t in ids]
    for index, entry in enumerate(payload.entries):
        if not entry.goal.strip():
            raise HTTPException(422, 'Each observation entry needs a goal.')
        if entry.prompts not in PROMPTS:
            raise HTTPException(422, 'Select a valid prompt.')
        normalized_level_id, normalized_domain_ids = validate_catalog_selection(db, entry.level_id, entry.title_id, entry.domain_ids, entry.skill_ids)
        title = db.get(GoalTitleMaster, entry.title_id) if entry.title_id else None
        values = entry.model_dump()
        domain_ids = values.pop('domain_ids')
        skill_ids = values.pop('skill_ids')
        values['level_id'] = normalized_level_id
        if title:
            values['objective'] = title.title
        values['goal'] = entry.goal.strip()
        child = PatientObservationSheetEntry(**values, sort_order=index)
        child.domains = [PatientObservationSheetEntryDomain(domain_id=d) for d in normalized_domain_ids]
        child.skills = [PatientObservationSheetEntrySkill(skill_id=s) for s in sorted(set(skill_ids))]
        row.entries.append(child)
    db.commit()
    return {'data': shape(row, 'observations', True)}
