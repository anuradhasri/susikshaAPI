"""Report persistence and child history tests using an isolated in-memory database."""
import unittest
from datetime import date
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import get_db
from app.models.models import User, Region, Patient, Therapist
from app.models.report_sheets import REPORT_TABLES, GoalLevelMaster, GoalTitleMaster, GoalDomainMaster, GoalSkillMaster, GoalTitleSkillMapping, GoalObjectiveTypeMaster, PatientObservationSheetEntry
from app.services.report_sheet_catalog import provision_report_sheets
from app.api.routes import report_sheets as routes


class ReportSheetTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        @event.listens_for(self.engine, 'connect')
        def configure(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.create_function('concat', -1, lambda *args: ''.join(str(a or '') for a in args))
        for model in [User, Region, Patient, Therapist] + REPORT_TABLES:
            model.__table__.create(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(id=1, username='test', email='test@example.test', hashed_password='unused', first_name='Test', last_name='User')
        self.db.add_all([self.user, Region(id=1, name='Centre One', code='ONE'), Region(id=2, name='Centre Two', code='TWO')])
        self.db.flush()
        self.db.add_all([Patient(id=1, first_name='Test', last_name='Child', date_of_birth=date(2020, 1, 1), region_id=1), Patient(id=2, first_name='Other', last_name='Child', date_of_birth=date(2020, 1, 1), region_id=2), Therapist(id=1, name='Therapist One', region_id=1), Therapist(id=2, name='Therapist Two', region_id=1), Therapist(id=3, name='Other Centre', region_id=2)])
        self.db.add_all([GoalLevelMaster(id=1, name='LEVEL 1'), GoalLevelMaster(id=2, name='LEVEL 2')])
        self.db.flush()
        self.db.add(GoalDomainMaster(id=1, level_id=1, code='A', description='Engagement domain'))
        self.db.flush()
        self.db.add_all([GoalTitleMaster(id=1, level_id=1, title='Engagement', description='Engage in play'), GoalSkillMaster(id=1, level_id=1, domain_id=1, code='A1', description='Participates'), GoalSkillMaster(id=2, level_id=1, domain_id=1, code='A2', description='Other skill'), GoalObjectiveTypeMaster(id=1, code='lang', name='Language Objective')])
        self.db.flush()
        self.db.add(GoalTitleSkillMapping(title_id=1, skill_id=1))
        self.db.commit()
        self.patches = [patch.object(routes, '_require_user', return_value=self.user), patch.object(routes, '_permission_shape', return_value={'menu.reports': {'view': True}, 'report.action.create_sheet': {'create': True}}), patch.object(routes, '_user_region_ids', return_value=[1])]
        for p in self.patches: p.start()
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.base = '/api/v1/ui/reports/sheets'

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def observation(self, day='2026-09-18', **overrides):
        payload = {'patient_id': 1, 'observation_date': day, 'therapist_ids': [1, 2], 'entries': [{'title_id': 1, 'goal': 'Child participates', 'accuracy': 75, 'accuracy_status': 'E', 'prompts': 'Modelling', 'activities': 'Play', 'responses': 'Participated', 'concerns': 'None', 'strategy': 'Visual routine'}]}
        payload.update(overrides)
        return self.client.post(self.base + '/observations', json=payload)

    def goal(self, **overrides):
        payload = {'patient_id': 1, 'planning_month': '2026-09-01', 'review_month': '2026-10-01', 'therapist_id': 1, 'parent_name': 'Test Parent', 'parental_objectives': 'Home practice', 'items': [{'level_id': 1, 'title_id': 1, 'description': 'Goal text', 'domain_ids': [1], 'skill_ids': [1], 'objectives': [{'type_id': 1, 'text': 'Language goal'}]}]}
        payload.update(overrides)
        return self.client.post(self.base + '/goals', json=payload)

    def test_multiple_observation_sheets_latest_date_first_and_pagination(self):
        for day in ['2026-09-18', '2026-08-03', '2026-09-18', '2026-10-02']:
            self.assertEqual(self.observation(day).status_code, 201)
        history = self.client.get(self.base + '/observations/history/1').json()['data']
        self.assertEqual([h['observation_date'] for h in history], ['2026-10-02', '2026-09-18', '2026-09-18', '2026-08-03'])
        self.assertGreater(history[1]['id'], history[2]['id'])
        listing = self.client.get(self.base + '/observations?page_size=2&page=2').json()
        self.assertEqual((listing['total'], listing['pages'], len(listing['data'])), (4, 2, 2))
        self.assertEqual(listing['data'][1]['observation_date'], '2026-08-03')

    def test_goals_save_all_fields_multiple_sheets_and_custom_goals(self):
        saved = self.goal()
        self.assertEqual(saved.status_code, 201, saved.text)
        record = self.client.get(self.base + '/goals/' + str(saved.json()['data']['id'])).json()['data']
        self.assertEqual(record['items'][0]['skill_ids'], [1])
        self.assertEqual(record['items'][0]['objectives'][0]['text'], 'Language goal')
        self.assertEqual(record['parent_name'], 'Test Parent')
        self.assertEqual(self.goal(items=[{'description': 'Custom goal'}]).status_code, 201)
        self.assertEqual(self.client.get(self.base + '/goals').json()['total'], 2)

    def test_goal_exact_planning_and_review_dates(self):
        response = self.goal(planning_month='2026-09-18', review_month='2026-09-25')
        self.assertEqual(response.status_code, 201, response.text)
        saved = self.client.get(self.base + '/goals/' + str(response.json()['data']['id'])).json()['data']
        self.assertEqual((saved['planning_month'], saved['review_month']), ('2026-09-18', '2026-09-25'))
        self.assertEqual(self.goal(planning_month='2026-09-18', review_month='2026-09-17').status_code, 422)
        self.assertEqual(self.client.get(self.base + '/goals?start_date=2026-09-18&end_date=2026-09-18').json()['total'], 1)
        self.assertEqual(self.client.get(self.base + '/goals?end_date=2026-09-17').json()['total'], 0)
        self.assertEqual(self.client.get(self.base + '/goals?month=2026-09').json()['total'], 1)

    def test_goal_multiple_therapists_save_reopen_and_filter(self):
        response = self.goal(therapist_ids=[2, 1, 2])
        self.assertEqual(response.status_code, 201, response.text)
        saved = self.client.get(self.base + '/goals/' + str(response.json()['data']['id'])).json()['data']
        self.assertEqual(saved['therapist_ids'], [1, 2])
        self.assertEqual(saved['therapist_names'], ['Therapist One', 'Therapist Two'])
        self.goal()
        self.assertEqual(self.client.get(self.base + '/goals?therapist_id=2').json()['total'], 1)
        self.assertEqual(self.goal(therapist_ids=[]).status_code, 422)
        self.assertEqual(self.goal(therapist_ids=[1, 3]).status_code, 422)
        self.assertEqual(self.goal(therapist_id=None).status_code, 422)

    def test_observation_objectives_come_from_child_saved_goal_titles(self):
        endpoint = self.base + '/observations/goal-titles/'
        self.assertEqual(self.client.get(endpoint + '1').json()['data'], [])
        self.goal(items=[{'level_id': 1, 'title_id': 1, 'description': 'Child-specific goal'}])
        self.goal(items=[{'level_id': 1, 'title_id': 1, 'description': 'Latest child-specific goal'}, {'description': 'Untitled goal'}])
        titles = self.client.get(endpoint + '1').json()['data']
        self.assertEqual(titles, [{'id': 1, 'level_id': 1, 'title': 'Engagement', 'description': 'Latest child-specific goal'}])
        self.assertEqual(self.client.get(endpoint + '2').status_code, 404)
        with patch.object(routes, '_user_region_ids', return_value=[1, 2]):
            self.assertEqual(self.client.get(endpoint + '2').json()['data'], [])

    def test_goal_legacy_therapist_backfill_is_idempotent(self):
        from app.models.report_sheets import PatientGoalSheetTherapist
        saved = self.goal().json()['data']
        self.db.query(PatientGoalSheetTherapist).delete()
        self.db.commit()
        self.assertEqual(self.client.get(self.base + '/goals/' + str(saved['id'])).json()['data']['therapist_ids'], [1])
        provision_report_sheets(self.db)
        provision_report_sheets(self.db)
        self.assertEqual(self.db.query(PatientGoalSheetTherapist).count(), 1)

    def test_observations_persist_all_fields_without_session_or_slot(self):
        response = self.observation()
        self.assertEqual(response.status_code, 201, response.text)
        saved = self.client.get(self.base + '/observations/' + str(response.json()['data']['id'])).json()['data']
        self.assertEqual(saved['therapist_ids'], [1, 2])
        entry = saved['entries'][0]
        self.assertEqual((entry['accuracy'], entry['accuracy_status'], entry['objective'], entry['strategy']), (75, 'E', 'Engagement', 'Visual routine'))
        title = self.db.get(GoalTitleMaster, 1)
        title.title = 'Changed title'
        self.db.commit()
        self.assertEqual(self.db.get(PatientObservationSheetEntry, entry['id']).objective, 'Engagement')

    def test_filters_and_invalid_date_range(self):
        self.observation('2026-09-18')
        self.observation('2026-08-03', therapist_ids=[1])
        data = self.client.get(self.base + '/observations?patient_id=1&therapist_id=2&start_date=2026-09-01&end_date=2026-09-30&search=Test%20Child').json()
        self.assertEqual(data['total'], 1)
        self.assertEqual(self.client.get(self.base + '/observations?region_id=2').json()['total'], 0)
        self.assertEqual(self.client.get(self.base + '/observations?start_date=2026-10-01&end_date=2026-01-01').status_code, 422)
        self.assertEqual(self.client.get(self.base + '/observations?page=0').status_code, 422)

    def test_cross_centre_and_permission_checks(self):
        self.assertEqual(self.observation(patient_id=2).status_code, 404)
        self.assertEqual(self.observation(therapist_ids=[3]).status_code, 422)
        self.assertEqual(self.client.get(self.base + '/observations/history/2').status_code, 404)
        with patch.object(routes, '_permission_shape', return_value={'menu.reports': {'view': True}}):
            self.assertEqual(self.observation().status_code, 403)
            self.assertEqual(self.client.get(self.base + '/observations').status_code, 200)
        with patch.object(routes, '_permission_shape', return_value={}):
            self.assertEqual(self.client.get(self.base + '/masters').status_code, 403)

    def test_listing_month_filter_boundaries_and_validation(self):
        for day in ['2026-08-31', '2026-09-01', '2026-09-30', '2026-10-01']:
            self.assertEqual(self.observation(day).status_code, 201)
        listing = self.client.get(self.base + '/observations?month=2026-09').json()
        self.assertEqual(listing['total'], 2)
        self.assertEqual([r['observation_date'] for r in listing['data']], ['2026-09-30', '2026-09-01'])
        self.assertEqual(self.client.get(self.base + '/observations?month=2026-09&start_date=2026-09-15').json()['total'], 1)
        self.assertEqual(self.goal().status_code, 201)
        self.assertEqual(self.client.get(self.base + '/goals?month=2026-09').json()['total'], 1)
        self.assertEqual(self.client.get(self.base + '/goals?month=2026-10').json()['total'], 0)
        for month in ['2026-13', '2026-00', '0000-01', 'invalid']:
            self.assertEqual(self.client.get(self.base + '/observations?month=' + month).status_code, 422)

    def test_invalid_accuracy_status_prompt_and_empty_entries(self):
        for entry in [{'goal': 'Goal', 'accuracy': 101}, {'goal': 'Goal', 'accuracy': -1}, {'goal': 'Goal', 'accuracy_status': 'X'}, {'goal': 'Goal', 'prompts': 'Unknown'}, {'goal': '   '}]:
            self.assertEqual(self.observation(entries=[entry]).status_code, 422)
        self.assertEqual(self.observation(entries=[]).status_code, 422)

    def test_master_hierarchy_and_invalid_skills(self):
        for item in [{'level_id': 2, 'title_id': 1, 'description': 'Goal'}, {'level_id': 1, 'title_id': 1, 'description': 'Goal', 'skill_ids': [999]}, {'description': 'Goal', 'skill_ids': [1]}]:
            self.assertEqual(self.goal(items=[item]).status_code, 422)
        self.assertEqual(self.goal(review_month='2026-08-01').status_code, 422)
        self.assertEqual(len(self.client.get(self.base + '/masters?region_id=1').json()['data']['patients']), 1)

    def test_catalog_migration_is_idempotent(self):
        provision_report_sheets(self.db)
        counts = [self.db.query(m).count() for m in REPORT_TABLES]
        provision_report_sheets(self.db)
        self.assertEqual(counts, [self.db.query(m).count() for m in REPORT_TABLES])


if __name__ == '__main__':
    unittest.main()
