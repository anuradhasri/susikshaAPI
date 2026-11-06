"""Report persistence and child history tests using an isolated in-memory database."""
import unittest
from datetime import date
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import get_db
from app.models.models import User, Region, Patient, Therapist
from app.models.report_sheets import REPORT_TABLES, GoalLevelMaster, GoalTitleMaster, GoalDomainMaster, GoalSkillMaster, GoalTitleSkillMapping, GoalObjectiveTypeMaster, PatientObservationSheetEntry, PatientGoalSummary, PatientGoalSheet, PatientObservationSheet
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
        self.patches = [patch.object(routes, '_require_user', return_value=self.user), patch.object(routes, '_permission_shape', return_value={'menu.sheets': {'view': True}, 'report.action.create_sheet': {'create': True}}), patch.object(routes, '_user_region_ids', return_value=[1]), patch.object(routes, '_user_roles', return_value=[])]
        for p in self.patches: p.start()
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.base = '/api/v1/ui/reports/sheets'

    def test_child_progress_consolidates_history_and_enforces_region(self):
        for day, answers in [('2026-10-01', [(1, 'E'), (2, 'A')]),
                             ('2026-10-20', [(1, 'A'), (2, 'NO')])]:
            response = self.client.post(self.base + '/summaries', json={
                'patient_id': 1, 'evaluation_date': day, 'therapist_ids': [1],
                'level_id': 1, 'responses': [{'skill_id': skill, 'status': status, 'comments': 'Saved note'}
                                           for skill, status in answers],
            })
            self.assertEqual(response.status_code, 201, response.text)
        result = self.client.get(self.base + '/child-progress/1')
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()['data']
        self.assertEqual(data['evaluation_count'], 2)
        self.assertEqual(data['latest_evaluation_date'], '2026-10-20')
        self.assertEqual({item['skill_id']: item['status'] for item in data['responses']}, {1: 'A', 2: 'NO'})
        self.assertIsNone(data['selected_summary_id'])
        self.assertEqual(len(data['evaluations']), 2)
        earlier_id = data['evaluations'][-1]['id']
        earlier = self.client.get(self.base + f'/child-progress/1?summary_id={earlier_id}').json()['data']
        self.assertEqual(earlier['selected_summary_id'], earlier_id)
        self.assertEqual(earlier['evaluation_count'], 1)
        self.assertEqual(earlier['latest_evaluation_date'], '2026-10-01')
        self.assertEqual({item['skill_id']: item['status'] for item in earlier['responses']}, {1: 'E', 2: 'A'})
        self.assertEqual(len(earlier['evaluations']), 2)
        self.assertEqual(self.client.get(self.base + '/child-progress/1?summary_id=9999').status_code, 404)
        with patch.object(routes, '_user_region_ids', return_value=[1, 2]):
            self.assertEqual(self.client.get(self.base + f'/child-progress/2?summary_id={earlier_id}').status_code, 404)
        self.assertEqual(self.client.get(self.base + '/child-progress/2').status_code, 404)
        self.assertEqual(self.client.get(self.base + '/child-progress/999').status_code, 404)
        with patch.object(routes, '_permission_shape', return_value={}):
            self.assertEqual(self.client.get(self.base + '/child-progress/1').status_code, 403)

    def test_child_progress_without_evaluations(self):
        data = self.client.get(self.base + '/child-progress/1').json()['data']
        self.assertEqual(data['evaluation_count'], 0)
        self.assertEqual(data['responses'], [])
        self.assertIsNone(data['latest_evaluation_date'])

    def test_entry_ownership_and_append_to_old_sheet(self):
        from datetime import datetime, timedelta
        created = self.observation().json()['data']
        original = created['entries'][0]
        self.assertEqual(original['created_by_name'], 'Test User')
        self.assertIsNone(original['updated_by_name'])
        payload = {'patient_id': 1, 'observation_date': created['observation_date'], 'therapist_ids': [1, 2], 'entries': [original]}
        payload['entries'][0]['goal'] = 'Changed by creator'
        edited = self.client.put(f"{self.base}/observations/{created['id']}", json=payload)
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json()['data']['entries'][0]['updated_by_name'], 'Test User')
        self.assertIsNotNone(edited.json()['data']['entries'][0]['updated_at'])
        other = User(id=9, username='other', email='other@example.test', hashed_password='unused', first_name='Other', last_name='User')
        self.db.add(other)
        self.db.commit()
        with patch.object(routes, '_require_user', return_value=other):
            payload['entries'][0]['goal'] = 'Unauthorized change'
            self.assertEqual(self.client.put(f"{self.base}/observations/{created['id']}", json=payload).status_code, 403)
        self.db.rollback()
        old_entry = self.db.get(PatientObservationSheetEntry, original['id'])
        old_entry.created_at = datetime.utcnow() - timedelta(days=2)
        self.db.get(PatientObservationSheet, created['id']).created_at = datetime.utcnow() - timedelta(days=2)
        self.db.commit()
        saved = self.client.get(f"{self.base}/observations/{created['id']}").json()['data']
        payload = {'patient_id': 1, 'observation_date': saved['observation_date'], 'therapist_ids': [1, 2], 'entries': saved['entries'] + [{'goal': 'New entry', 'prompts': ''}]}
        with patch.object(routes, '_require_user', return_value=other):
            response = self.client.put(f"{self.base}/observations/{created['id']}", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['data']['entries'][-1]['created_by'], 9)
        payload['entries'][0]['goal'] = 'Too late'
        self.assertEqual(self.client.put(f"{self.base}/observations/{created['id']}", json=payload).status_code, 403)

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
        self.assertEqual(titles, [{'id': 1, 'level_id': 1, 'title': 'Engagement', 'description': 'Latest child-specific goal', 'domain_ids': [], 'skill_ids': [], 'objectives': []}])
        self.assertEqual(self.client.get(endpoint + '2').status_code, 404)
        with patch.object(routes, '_user_region_ids', return_value=[1, 2]):
            self.assertEqual(self.client.get(endpoint + '2').json()['data'], [])

    def test_goal_reference_defaults_to_latest_active_and_does_not_mix_sheets(self):
        old = self.goal(planning_month='2026-08-01', review_month='2026-08-31', items=[{
            'level_id': 1, 'title_id': 1, 'description': 'Old goal', 'domain_ids': [1], 'skill_ids': [2],
        }]).json()['data']
        current = self.goal(planning_month='2026-10-01', review_month='2026-11-30', items=[{
            'level_id': 1, 'title_id': 1, 'description': 'Current goal', 'domain_ids': [1], 'skill_ids': [1],
        }, {'level_id': 2, 'description': 'Current custom goal'}]).json()['data']
        future = self.goal(planning_month='2026-12-01', review_month='2027-02-01').json()['data']
        endpoint = self.base + '/observations/goal-titles/1?as_of=2026-10-30'
        data = self.client.get(endpoint).json()
        self.assertEqual(data['selected_sheet_id'], current['id'])
        self.assertEqual(data['default_sheet_id'], current['id'])
        self.assertEqual(data['skill_ids'], [1])
        self.assertEqual(data['level_ids'], [1, 2])
        self.assertEqual(data['data'][0]['description'], 'Current goal')
        self.assertEqual(len(data['sheets']), 3)
        selected = self.client.get(endpoint + f"&goal_sheet_id={old['id']}").json()
        self.assertEqual(selected['skill_ids'], [2])
        self.assertEqual(selected['level_ids'], [1])
        self.assertEqual(selected['data'][0]['description'], 'Old goal')
        fallback = self.client.get(self.base + '/observations/goal-titles/1?as_of=2027-03-01').json()
        self.assertEqual(fallback['selected_sheet_id'], future['id'])
        self.assertFalse(any(sheet['is_active'] for sheet in fallback['sheets']))
        self.assertEqual(self.client.get(endpoint + '&goal_sheet_id=9999').status_code, 404)
        with patch.object(routes, '_user_region_ids', return_value=[1, 2]):
            self.assertEqual(self.client.get(self.base + f"/observations/goal-titles/2?goal_sheet_id={current['id']}").status_code, 404)

    def test_goal_reference_merges_same_title_only_within_selected_sheet(self):
        saved = self.goal(items=[
            {'level_id': 1, 'title_id': 1, 'description': 'First', 'domain_ids': [1], 'skill_ids': [1]},
            {'level_id': 1, 'title_id': 1, 'description': 'Second', 'domain_ids': [1], 'skill_ids': [2]},
        ]).json()['data']
        data = self.client.get(self.base + f"/observations/goal-titles/1?goal_sheet_id={saved['id']}").json()
        self.assertEqual(data['data'][0]['skill_ids'], [1, 2])
        self.assertEqual(data['data'][0]['description'], 'First\nSecond')

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
        with patch.object(routes, '_permission_shape', return_value={'menu.sheets': {'view': True}}):
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



    def test_sheet_listings_ignore_orphaned_patient_rows(self):
        goal = PatientGoalSheet(
            patient_id=1, region_id=1, planning_month=date(2026, 9, 1),
            review_month=date(2026, 10, 1), therapist_id=1,
            parent_name='', parental_objectives='',
            created_by=self.user.id, updated_by=self.user.id,
        )
        observation = PatientObservationSheet(
            patient_id=1, region_id=1, observation_date=date(2026, 9, 18),
            created_by=self.user.id, updated_by=self.user.id,
        )
        self.db.add_all([goal, observation])
        self.db.commit()
        goal_id, observation_id = goal.id, observation.id

        self.db.execute(text('PRAGMA foreign_keys=OFF'))
        self.db.execute(text('DELETE FROM patients WHERE id = 1'))
        self.db.commit()
        self.db.expire_all()

        for kind, row_id in [('goals', goal_id), ('observations', observation_id)]:
            listing = self.client.get(self.base + f'/{kind}').json()
            self.assertEqual((listing['total'], listing['data']), (0, []))
            self.assertEqual(self.client.get(self.base + f'/{kind}/{row_id}').status_code, 404)

    def test_goal_summary_listing_ignores_orphaned_patient_rows(self):
        summary = PatientGoalSummary(
            patient_id=1, region_id=1, evaluation_date=date(2026, 9, 18),
            therapist_id=1, informant='', level_id=1,
            created_by=self.user.id, updated_by=self.user.id,
        )
        self.db.add(summary)
        self.db.commit()

        # Reproduce a legacy database where foreign keys were not enforced.
        self.db.execute(text('PRAGMA foreign_keys=OFF'))
        self.db.execute(text('DELETE FROM patients WHERE id = 1'))
        self.db.commit()
        self.db.expire_all()

        listing = self.client.get(self.base + '/summaries').json()
        self.assertEqual((listing['total'], listing['data']), (0, []))
        self.assertEqual(self.client.get(self.base + f'/summaries/{summary.id}').status_code, 404)

if __name__ == '__main__':
    unittest.main()
