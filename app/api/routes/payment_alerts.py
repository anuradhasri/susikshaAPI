from datetime import date, datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.api.routes.ui import _require_user, _user_roles, _user_region_ids
from app.models.models import Region, Program
from app.models.payment_alerts import PaymentAlertRule, provision_payment_alerts
from app.services.payment_alerts import STAFF, GLOBAL, KINDS, defaults, eligible_users, reconcile_rule

router = APIRouter(prefix='/api/v1/ui/payment-alerts', tags=['payment alert settings'])


class RuleConfig(BaseModel):
    interval_days: int = Field(10, ge=1, le=365)
    period_days: int = Field(10, ge=1, le=365)
    start_date: date
    session_count: int = Field(3, ge=1, le=100)
    after_days: int = Field(4, ge=1, le=365)
    include_partial: bool = False
    plan_types: list[Literal['fixed', 'package']] = Field(default_factory=lambda: ['fixed', 'package'], max_length=2)
    program_ids: list[int] = Field(default_factory=list, max_length=200)


class RuleInput(BaseModel):
    kind: Literal['cash_summary', 'unpaid_sessions', 'unpaid_plans']
    enabled: bool = False
    recipients: list[int] = Field(default_factory=list, max_length=100)
    config: RuleConfig


def access(request, db, region_id=None):
    user = _require_user(request, db)
    roles = {str(role).lower().replace(' ', '_').replace('-', '_') for role in _user_roles(db, user)}
    if not roles & STAFF:
        raise HTTPException(403, 'Only centre administration can configure payment alerts.')
    ids = _user_region_ids(db, user)
    if not ids and not roles & GLOBAL:
        raise HTTPException(403, 'No centre is assigned to your account.')
    regions = db.query(Region)
    if ids:
        regions = regions.filter(Region.id.in_(ids))
    regions = regions.order_by(Region.name).all()
    if region_id and region_id not in {region.id for region in regions}:
        raise HTTPException(404, 'Centre not found.')
    return user, regions


def shape(rule):
    return {'kind': rule.kind, 'enabled': rule.enabled, 'recipients': rule.recipients, 'config': rule.config}


@router.get('')
def settings(request: Request, region_id: int | None = None, db: Session = Depends(get_db)):
    _, regions = access(request, db, region_id)
    selected = region_id or (regions[0].id if regions else None)
    provision_payment_alerts(db)
    rules = {rule.kind: shape(rule) for rule in db.query(PaymentAlertRule).filter_by(region_id=selected).all()}
    return {'data': {'regions': [{'id': region.id, 'name': region.name} for region in regions], 'region_id': selected,
        'recipients': [{'id': user.id, 'name': f'{user.first_name or ""} {user.last_name or ""}'.strip() or user.username} for user in eligible_users(db, selected)] if selected else [],
        'programs': [{'id': row.id, 'name': row.program_name} for row in db.query(Program).filter_by(region_id=selected, is_active=True).all()] if selected else [],
        'rules': [rules.get(kind, {'kind': kind, 'enabled': False, 'recipients': [], 'config': defaults(kind)}) for kind in KINDS]}}


def validate(request, db, region_id, payload):
    user, _ = access(request, db, region_id)
    if payload.enabled and not payload.recipients:
        raise HTTPException(422, 'Choose at least one recipient before enabling this alert.')
    allowed = {user.id for user in eligible_users(db, region_id)}
    if not set(payload.recipients) <= allowed:
        raise HTTPException(422, 'Select active administration recipients with access to this centre.')
    programs = {row.id for row in db.query(Program).filter(Program.region_id == region_id).all()}
    if not set(payload.config.program_ids) <= programs:
        raise HTTPException(422, 'Select programs belonging to this centre.')
    if payload.kind == 'unpaid_plans' and not payload.config.plan_types:
        raise HTTPException(422, 'Select Fixed, Package, or both plan types.')
    return user


@router.put('/{region_id}')
def save(region_id: int, payload: RuleInput, request: Request, db: Session = Depends(get_db)):
    user = validate(request, db, region_id, payload)
    provision_payment_alerts(db)
    rule = db.query(PaymentAlertRule).filter_by(region_id=region_id, kind=payload.kind).first()
    if not rule:
        rule = PaymentAlertRule(region_id=region_id, kind=payload.kind)
        db.add(rule)
    rule.enabled = payload.enabled; rule.recipients = sorted(set(payload.recipients))
    rule.config = payload.config.model_dump(mode='json'); rule.updated_by = user.id
    db.commit(); db.refresh(rule)
    return {'data': shape(rule)}


@router.post('/{region_id}/preview')
def preview(region_id: int, payload: RuleInput, request: Request, db: Session = Depends(get_db)):
    validate(request, db, region_id, payload)
    rule = PaymentAlertRule(region_id=region_id, kind=payload.kind, config=payload.config.model_dump(mode='json'))
    return {'data': reconcile_rule(db, rule, datetime.now(timezone.utc), preview=True)}
