import html
import json
import logging
import smtplib
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from sqlalchemy.orm import joinedload, selectinload

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.models import Role, Therapist, User, UserRegionMapping, UserRole
from app.models.report_sheets import (
    PatientGoalSheet,
    GoalReviewEmailLog,
    PatientGoalSheetTherapist,
)

logger = logging.getLogger(__name__)
settings = get_settings()
FRONT_OFFICE_ROLES = {'front_office', 'frontoffice', 'front_officer'}


def _today() -> date:
    return datetime.now(ZoneInfo(settings.GOAL_REVIEW_REMINDER_TIMEZONE)).date()


def _front_office_emails(db, region_id: int) -> list[str]:
    rows = (
        db.query(User.email)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .join(UserRegionMapping, UserRegionMapping.userid == User.id)
        .filter(
            UserRegionMapping.regionid == region_id,
            Role.name.in_(FRONT_OFFICE_ROLES),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            UserRole.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
        .distinct()
        .all()
    )
    return sorted({email.strip() for (email,) in rows if email and email.strip()})


def _message(therapist_name: str, therapist_email: str, region_name: str, cc: list[str], sheets: list[PatientGoalSheet]) -> EmailMessage:
    today = _today()
    subject = f'Goal Sheet review reminder - {region_name}'
    text_sections = []
    html_sections = []
    for sheet in sorted(sheets, key=lambda value: (value.review_month, value.patient.first_name, value.id)):
        child_name = f'{sheet.patient.first_name} {sheet.patient.last_name}'.strip()
        days_left = (sheet.review_month - today).days
        day_label = 'day' if days_left == 1 else 'days'
        timing = 'today' if days_left == 0 else f'in {days_left} {day_label}'
        goals = [item.description.strip() for item in sheet.items if item.description and item.description.strip()]
        text_sections.append(
            f'{child_name}\nReview date: {sheet.review_month.strftime("%d/%m/%Y")} ({timing})\n' +
            '\n'.join(f'{index}. {goal}' for index, goal in enumerate(goals, 1))
        )
        html_sections.append(
            '<section style="margin:16px 0;padding:16px;border:1px solid #dbe6f5;border-radius:10px;background:#f8fbff">'
            f'<h3 style="margin:0 0 6px;color:#142d52">{html.escape(child_name)}</h3>'
            f'<p style="margin:0 0 12px;color:#52657f"><strong>Review date:</strong> {sheet.review_month.strftime("%d/%m/%Y")} ({html.escape(timing)})</p>'
            '<ol style="margin:0;padding-left:22px;color:#1f2937">' +
            ''.join(f'<li style="margin:7px 0;line-height:1.5">{html.escape(goal)}</li>' for goal in goals) +
            '</ol></section>'
        )

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = settings.email_from
    message['To'] = therapist_email
    if cc:
        message['Cc'] = ', '.join(cc)
    message.set_content(
        f'Hello {therapist_name},\n\nThe following Goal Sheets assigned to you are due for review within the next seven days.\n\n' +
        '\n\n'.join(text_sections) +
        '\n\nPlease review and update them on time.\n\nSushiksha Intervention Centre'
    )
    message.add_alternative(
        '<html><body style="margin:0;background:#f3f7fd;font-family:Arial,sans-serif;color:#1f2937">'
        '<div style="max-width:680px;margin:24px auto;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #dbe6f5">'
        '<div style="background:#116eb7;padding:20px 24px;color:#fff"><h2 style="margin:0">Goal Sheet Review Reminder</h2></div>'
        f'<div style="padding:24px"><p>Hello <strong>{html.escape(therapist_name)}</strong>,</p>'
        '<p>The following Goal Sheets assigned to you are due for review within the next seven days:</p>' +
        ''.join(html_sections) +
        '<p style="margin-top:20px">Please review and update them on time.</p>'
        '<p style="margin:24px 0 0;color:#64748b">Sushiksha Intervention Centre</p></div></div></body></html>',
        subtype='html',
    )
    return message


def _send(message: EmailMessage) -> None:
    smtp_class = smtplib.SMTP_SSL if settings.email_use_ssl else smtplib.SMTP
    with smtp_class(settings.email_host, settings.email_port, timeout=30) as smtp:
        if settings.email_use_tls:
            smtp.starttls()
        if settings.email_username:
            smtp.login(settings.email_username, settings.email_password)
        smtp.send_message(message)


def send_goal_review_reminders() -> dict[str, int]:
    """Email assigned therapists about Goal Sheets due from today through seven days ahead."""
    if not settings.email_host:
        logger.warning('Goal review reminders skipped because SMTP is not configured.')
        return {'sheets': 0, 'emails': 0, 'skipped': 1}

    today = _today()
    deadline = today + timedelta(days=7)
    db = SessionLocal()
    try:
        GoalReviewEmailLog.__table__.create(db.get_bind(), checkfirst=True)
        sheets = (
            db.query(PatientGoalSheet)
            .options(
                joinedload(PatientGoalSheet.patient),
                joinedload(PatientGoalSheet.region),
                selectinload(PatientGoalSheet.therapists)
                .selectinload(PatientGoalSheetTherapist.therapist)
                .joinedload(Therapist.user),
                selectinload(PatientGoalSheet.items),
            )
            .filter(PatientGoalSheet.review_month >= today, PatientGoalSheet.review_month <= deadline)
            .all()
        )
        grouped = defaultdict(list)
        therapist_names = {}
        therapist_ids = {}
        regions = {}
        for sheet in sheets:
            regions[sheet.region_id] = sheet.region.name
            for assignment in sheet.therapists:
                therapist = assignment.therapist
                if therapist and therapist.user and therapist.user.email and therapist.user.is_active:
                    key = (therapist.user.email.strip(), sheet.region_id)
                    grouped[key].append(sheet)
                    therapist_names[key] = therapist.name
                    therapist_ids[key] = therapist.id

        sent = 0
        for (email, region_id), assigned_sheets in grouped.items():
            cc = [address for address in _front_office_emails(db, region_id) if address.casefold() != email.casefold()]
            message = _message(therapist_names[(email, region_id)], email, regions[region_id], cc, assigned_sheets)
            plain_body = message.get_body(preferencelist=('plain',))
            log = GoalReviewEmailLog(
                run_date=today,
                region_id=region_id,
                therapist_id=therapist_ids[(email, region_id)],
                to_email=email,
                cc_emails=json.dumps(cc),
                sheet_ids=json.dumps(sorted({sheet.id for sheet in assigned_sheets})),
                subject=str(message['Subject']),
                content=plain_body.get_content() if plain_body else message.as_string(),
                status='pending',
            )
            db.add(log)
            db.commit()
            try:
                _send(message)
                log.status = 'sent'
                log.sent_at = datetime.now(ZoneInfo(settings.GOAL_REVIEW_REMINDER_TIMEZONE))
                sent += 1
            except Exception as exc:
                log.status = 'failed'
                log.error_message = str(exc)[:4000]
                logger.exception('Goal review reminder failed for therapist %s.', therapist_ids[(email, region_id)])
            db.commit()
        logger.info('Goal review reminders sent: %s emails covering %s sheets.', sent, len(sheets))
        return {'sheets': len(sheets), 'emails': sent, 'skipped': 0}
    finally:
        db.close()


def send_scheduled_goal_review_reminders() -> dict[str, int]:
    """Run the configured reminder check without duplicate suppression."""
    return send_goal_review_reminders()
