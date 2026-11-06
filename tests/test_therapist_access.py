import unittest
from datetime import date
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.middleware.middleware import RegionAccessMiddleware
from app.models.models import Therapist, Role, UserRole
from app.models.payouts import TherapistSessionPayout
from app.api.routes import payouts
from app.services.therapist_access import is_limited_therapist, therapist_request_allowed
from tests import test_program_operations as fixture


class TherapistAccessTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ProgramOperationsTests(); self.fixture.setUp()
        self.db = self.fixture.db; self.user = self.fixture.user

    def tearDown(self):
        self.fixture.tearDown()

    def test_role_aliases_resolve_to_existing_canonical_roles(self):
        from app.api.routes.ui import _role
        from fastapi import HTTPException
        role = Role(name='front_office')
        self.db.add(role); self.db.commit()
        for alias in ('FrontOffice', 'front_officer', 'front_office'):
            self.assertEqual(_role(self.db, alias).id, role.id)
        with self.assertRaises(HTTPException):
            _role(self.db, 'unexpected_role')

    def test_role_and_endpoint_allowlist(self):
        self.assertTrue(is_limited_therapist({'therapist'}))
        self.assertTrue(is_limited_therapist({'assessment_session'}))
        self.assertTrue(is_limited_therapist({'assessment_viewer_session'}))
        self.assertFalse(is_limited_therapist({'admin', 'therapist'}))
        for path in ('/api/v1/ui/reports/sheets/goals', '/api/v1/ui/reports/sheets/observations/1',
                     '/api/v1/ui/reports/sheets/summaries/1', '/api/v1/ui/reports/sheets/masters',
                     '/api/v1/masters/regions', '/api/v1/payouts/my-earnings', '/api/v1/appointments/calendar', '/api/v1/appointments/slots', '/api/v1/appointments/programs'):
            self.assertTrue(therapist_request_allowed('GET', path), path)
        for path in ('/api/v1/payouts/dashboard', '/api/v1/payouts/billing-reports', '/api/v1/ui/patients',
                     '/api/v1/ui/reports/sheets/child-progress/1', '/api/v1/ui/payment-alerts', '/api/v1/appointments'):
            self.assertFalse(therapist_request_allowed('GET', path), path)

        self.assertFalse(therapist_request_allowed('POST', '/api/v1/appointments/slot-booking'))

    def test_earnings_are_server_scoped_and_exclude_centre_financials(self):
        self.db.get(Therapist, 1).user_id = self.user.id
        self.fixture.booking(1, 1); self.fixture.booking(2, 1, therapist=2)
        for identifier, therapist, amount in [(1, 1, 100), (2, 2, 900)]:
            self.db.add(TherapistSessionPayout(id=identifier, patient_slot_booking_id=identifier,
                therapist_id=therapist, program_id=1, config_id=1, session_date=date(2026, 10, 10),
                eligible_amount=1000, gst_amount=100, amount_after_gst=900, therapist_amount=amount,
                organization_amount=900 - amount, calculation_json='{}', eligibility_status='eligible'))
        self.db.commit()
        app = FastAPI(); app.include_router(payouts.router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        with TestClient(app) as client:
            response = client.get('/api/v1/payouts/my-earnings?month=2026-10&therapist_id=2')
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()['data']
            self.assertEqual(data['eligibleEarnings'], 100)
            self.assertEqual(len(data['rows']), 1)
            self.assertEqual(data['rows'][0]['amount'], 100)
            self.assertEqual(set(data['rows'][0]), {'date', 'program', 'amount', 'status'})
            self.db.get(Therapist, 1).user_id = None; self.db.commit()
            self.assertEqual(client.get('/api/v1/payouts/my-earnings?month=2026-10').status_code, 403)

    def test_middleware_blocks_unrelated_api_even_with_old_token_roles(self):
        from sqlalchemy.orm import sessionmaker
        self.db.add_all([Role(id=1, name='therapist'), UserRole(id=1, user_id=1, role_id=1)])
        self.db.commit()
        app = FastAPI(); app.add_middleware(RegionAccessMiddleware)
        @app.get('/api/v1/payouts/dashboard')
        def forbidden(): return {'unexpected': True}
        @app.get('/api/v1/payouts/my-earnings')
        def permitted(): return {'own': True}
        factory = sessionmaker(bind=self.fixture.engine)
        with TestClient(app) as client, patch('app.middleware.middleware.decode_token', return_value={'user_id': 1, 'roles': []}), \
             patch('app.core.database.SessionLocal', side_effect=lambda: factory()):
            headers = {'Authorization': 'Bearer test'}
            self.assertEqual(client.get('/api/v1/payouts/dashboard', headers=headers).status_code, 403)
            self.assertEqual(client.get('/api/v1/payouts/my-earnings', headers=headers).status_code, 200)
