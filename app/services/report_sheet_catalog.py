import json
from pathlib import Path
from sqlalchemy import inspect, text
from app.models.report_sheets import (
    REPORT_TABLES, GoalLevelMaster, GoalTitleMaster, GoalDomainMaster, GoalSkillMaster,
    GoalTitleSkillMapping, GoalObjectiveTypeMaster, PatientGoalSheet, PatientGoalSheetTherapist,
    PatientGoalSummary, PatientGoalSummaryTherapist,
)


def provision_report_sheets(db):
    """Explicit additive migration; safe to rerun without replacing existing records."""
    for model in REPORT_TABLES:
        model.__table__.create(db.get_bind(), checkfirst=True)
    inspector = inspect(db.get_bind())
    additive_columns = {
        'goal_skill_master': [('level_id', 'INT NULL' if db.get_bind().dialect.name == 'mysql' else 'INTEGER'),
                              ('domain_id', 'INT NULL' if db.get_bind().dialect.name == 'mysql' else 'INTEGER')],
        'patient_observation_sheet_entry': [('level_id', 'INT NULL' if db.get_bind().dialect.name == 'mysql' else 'INTEGER')],
    }
    for table_name, columns in additive_columns.items():
        existing = {column['name'] for column in inspector.get_columns(table_name)}
        for column_name, column_type in columns:
            if column_name not in existing:
                db.execute(text(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}'))
    existing_pairs = {(r.sheet_id, r.therapist_id) for r in db.query(PatientGoalSheetTherapist).all()}
    for sheet in db.query(PatientGoalSheet.id, PatientGoalSheet.therapist_id).all():
        if (sheet.id, sheet.therapist_id) not in existing_pairs:
            db.add(PatientGoalSheetTherapist(sheet_id=sheet.id, therapist_id=sheet.therapist_id))
    summary_pairs = {(r.summary_id, r.therapist_id) for r in db.query(PatientGoalSummaryTherapist).all()}
    for summary in db.query(PatientGoalSummary.id, PatientGoalSummary.therapist_id).all():
        if (summary.id, summary.therapist_id) not in summary_pairs:
            db.add(PatientGoalSummaryTherapist(summary_id=summary.id, therapist_id=summary.therapist_id))
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
    domains = {(r.level_id, r.code): r for r in db.query(GoalDomainMaster).all()}
    for index, source in enumerate(catalog.get('code_skills', [])):
        level = levels[source['level']]
        key = (level.id, source['alpha'])
        domain = domains.get(key)
        if not domain:
            domain = GoalDomainMaster(level_id=level.id, code=source['alpha'], description=source['alphaDesc'], sort_order=index)
            db.add(domain)
            db.flush()
            domains[key] = domain
    skills = {(r.code, r.description): r for r in db.query(GoalSkillMaster).all()}
    for source in catalog.get('code_skills', []):
        level = levels[source['level']]
        domain = domains[(level.id, source['alpha'])]
        key = (source['code'], source['skill'])
        skill = skills.get(key)
        if not skill:
            skill = GoalSkillMaster(code=source['code'], description=source['skill'], level_id=level.id, domain_id=domain.id)
            db.add(skill)
            db.flush()
            skills[key] = skill
        elif skill.level_id != level.id or skill.domain_id != domain.id:
            skill.level_id = level.id
            skill.domain_id = domain.id
    mapping_rows = db.query(GoalTitleSkillMapping).all()
    mappings = {(r.title_id, r.skill_id) for r in mapping_rows}
    desired_mappings = set()
    mapping_skills = {(r.level_id, r.code, r.description): r for r in db.query(GoalSkillMaster).all()}
    for source in catalog['skills']:
        level = levels.get(source.get('level'))
        if not level:
            continue
        title = titles.get((level.id, source['title']))
        skill = mapping_skills.get((level.id, source['code'], source['skillDesc']))
        if not title or not skill:
            continue
        pair = (title.id, skill.id)
        desired_mappings.add(pair)
        if pair not in mappings:
            db.add(GoalTitleSkillMapping(title_id=title.id, skill_id=skill.id))
            mappings.add(pair)
    catalog_title_ids = {title.id for title in titles.values()}
    for mapping in mapping_rows:
        pair = (mapping.title_id, mapping.skill_id)
        if mapping.title_id in catalog_title_ids and pair not in desired_mappings:
            db.delete(mapping)
    types = {r.code for r in db.query(GoalObjectiveTypeMaster).all()}
    for code, name in [('lang', 'Language Objective'), ('pt', 'PT Objective'), ('ot', 'OT Objective'), ('academic', 'Academic Objective')]:
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
