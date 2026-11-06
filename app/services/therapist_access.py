import re
from app.models.models import Role, UserRole

THERAPIST_RESOURCES = {'menu.appointments', 'menu.sheets', 'report.action.create_sheet', 'menu.my_earnings'}
PRIVILEGED_ROLES = {'admin', 'front_office', 'frontoffice', 'front_officer', 'central_head', 'centralhead'}
THERAPIST_ROLES = {'therapist', 'assessment_session', 'assessment_viewer_session'}


def is_limited_therapist(roles):
    roles = {str(role).strip().lower().replace(' ', '_').replace('-', '_') for role in roles}
    return bool(roles & THERAPIST_ROLES) and not roles & PRIVILEGED_ROLES


def user_is_limited_therapist(db, user_id):
    roles = [name for (name,) in db.query(Role.name).join(UserRole, UserRole.role_id == Role.id).filter(
        UserRole.user_id == user_id, UserRole.deleted_at.is_(None), Role.deleted_at.is_(None)).all()]
    return is_limited_therapist(roles)


def therapist_request_allowed(method, path):
    path = path.rstrip('/')
    if path.startswith(('/api/v1/ui/auth/', '/api/v1/auth/')):
        return True
    if method == 'GET' and path in {'/api/v1/appointments/calendar', '/api/v1/appointments/slots', '/api/v1/appointments/programs', '/api/v1/masters/payment-modes', '/api/v1/session-plans/therapies', '/api/v1/masters/regions', '/api/v1/ui/regions', '/api/v1/payouts/my-earnings', '/api/v1/payouts/notifications'}:
        return True
    if method in {'GET', 'POST', 'PUT'} and re.fullmatch(
        r'/api/v1/ui/reports/sheets/(?:masters|(?:goals|observations|summaries)(?:/\d+)?|observations/(?:history|goal-titles)/\d+)', path):
        return True
    if method == 'POST' and re.fullmatch(r'/api/v1/payouts/(?:sheets/\d+/submit|notifications/(?:read-all|(?:sheets/)?\d+/read))', path):
        return True
    return False
