import json
from pathlib import Path
from sqlalchemy import inspect, text
from app.models.report_sheets import (
    REPORT_TABLES, GoalLevelMaster, GoalTitleMaster, GoalSkillMaster,
    GoalTitleSkillMapping, GoalObjectiveTypeMaster, PatientGoalSheet, PatientGoalSheetTherapist,
)


def provision_report_sheets(db):
    """Explicit additive migration; safe to rerun without replacing existing records."""
    for model in REPORT_TABLES:
        model.__table__.create(db.get_bind(), checkfirst=True)
    existing_pairs = {(r.sheet_id, r.therapist_id) for r in db.query(PatientGoalSheetTherapist).all()}
    for sheet in db.query(PatientGoalSheet.id, PatientGoalSheet.therapist_id).all():
        if (sheet.id, sheet.therapist_id) not in existing_pairs:
            db.add(PatientGoalSheetTherapist(sheet_id=sheet.id, therapist_id=sheet.therapist_id))
    catalog = json.loads((Path(__file__).resolve().parents[1] / 'report_sheet_catalog.json').read_text(encoding='utf-8'))
    levels = {r.name: r for r in db.query(GoalLevelMaster).all()}
    titles = {(r.level_id, r.title): r for r in db.query(GoalTitleMaster).all()}
    for goal in catalog['goals']:
        level = levels.get(goal['level'])
        if not level:
            level = GoalLevelMaster(name=goal['level'], sort_order=len(levels) + 1)
            db.add(level)
            db.flush()
            levels[level.name] = level
        key = (level.id, goal['title'])
        if key not in titles:
            title = GoalTitleMaster(level_id=level.id, title=goal['title'], description=goal['description'])
            db.add(title)
            db.flush()
            titles[key] = title
    skills = {(r.code, r.description): r for r in db.query(GoalSkillMaster).all()}
    mappings = {(r.title_id, r.skill_id) for r in db.query(GoalTitleSkillMapping).all()}
    for source in catalog['skills']:
        key = (source['code'], source['skillDesc'])
        skill = skills.get(key)
        if not skill:
            skill = GoalSkillMaster(code=key[0], description=key[1])
            db.add(skill)
            db.flush()
            skills[key] = skill
        for title in titles.values():
            pair = (title.id, skill.id)
            if title.title == source['title'] and pair not in mappings:
                db.add(GoalTitleSkillMapping(title_id=title.id, skill_id=skill.id))
                mappings.add(pair)
    types = {r.code for r in db.query(GoalObjectiveTypeMaster).all()}
    for code, name in [('lang', 'Language Objective'), ('pt', 'PT Objective'), ('ot', 'OT Objective')]:
        if code not in types:
            db.add(GoalObjectiveTypeMaster(code=code, name=name))
    if db.get_bind().dialect.name == 'mysql' and inspect(db.get_bind()).has_table('rbac_resources'):
        db.execute(text("""
            INSERT IGNORE INTO rbac_resources (code, resource_type, label, parent_code, display_order, is_active)
            VALUES ('report.action.create_sheet', 'action', 'Add report sheet', 'menu.reports', 55, 1)
        """))
        db.execute(text("""
            INSERT IGNORE INTO rbac_role_permissions (role_id, resource_id, can_view, can_create, can_edit, can_delete)
            SELECT report_permission.role_id, new_resource.id, 1, 1, 0, 0
            FROM rbac_role_permissions report_permission
            JOIN rbac_resources report_resource ON report_resource.id = report_permission.resource_id
            JOIN roles role ON role.id = report_permission.role_id
            JOIN rbac_resources new_resource ON new_resource.code = 'report.action.create_sheet'
            WHERE report_resource.code = 'menu.reports' AND report_permission.can_view = 1
              AND role.name IN ('admin', 'front_office', 'frontoffice', 'front_officer')
              AND role.deleted_at IS NULL
        """))
    db.commit()
