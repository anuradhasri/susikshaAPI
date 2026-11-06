import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests import test_program_operations as fixture
from app.core.database import get_db
from app.models.models import Payment, PatientPackage, Package, Notification, PaymentModeMaster, CrtProgramBooking, GroupProgramBooking, Role, UserRole, UserRegionMapping
from app.models.payouts import PatientProgramPricing
from app.models.payment_alerts import PaymentAlertRule, PaymentAlertEvent, PaymentAlertLease
from app.services import payment_alerts as service
from app.api.routes import payment_alerts as routes


class PaymentAlertTests(unittest.TestCase):
    def test_application_route_order_selects_alert_handlers_before_generic_tables(self):
        from app.api.routes import routers
        from starlette.routing import Match
        app = FastAPI()
        for router in routers:
            app.include_router(router)
        for method, path, endpoint in [
            ('GET', '/api/v1/ui/payment-alerts', routes.settings),
            ('PUT', '/api/v1/ui/payment-alerts/1', routes.save),
            ('POST', '/api/v1/ui/payment-alerts/1/preview', routes.preview),
        ]:
            scope = {'type': 'http', 'method': method, 'path': path, 'root_path': ''}
            match = next(route for route in app.routes if route.matches(scope)[0] == Match.FULL)
            self.assertIs(match.endpoint, endpoint)

    def setUp(self):
        self.fixture = fixture.ProgramOperationsTests()
        self.fixture.setUp()
        self.db = self.fixture.db
        self.now = datetime(2026, 10, 30, 6, tzinfo=timezone.utc)
        self.recipients = patch.object(service, 'eligible_users', return_value=[self.fixture.user])
        self.recipients.start()

    def tearDown(self):
        self.recipients.stop()
        self.fixture.tearDown()

    def rule(self, kind, **config):
        rule = PaymentAlertRule(region_id=1, kind=kind, enabled=True, recipients=[1],
                                config={**service.defaults(kind, date(2026, 10, 1)), **config})
        self.db.add(rule); self.db.commit()
        return rule

    def test_session_threshold_deduplication_payment_update_and_resolution(self):
        bookings = [self.fixture.booking(index, 1) for index in (1, 2, 3)]
        for row in bookings:
            row.amount = 100; row.due_amount = 100; row.paid_amount = 0
        self.db.commit()
        rule = self.rule('unpaid_sessions')
        service.reconcile_rule(self.db, rule, self.now)
        service.reconcile_rule(self.db, rule, self.now)
        self.assertEqual(self.db.query(Notification).count(), 1)
        self.assertIn('3 completed unpaid sessions', self.db.query(Notification).first().message)
        self.assertEqual(self.db.query(Notification).first().data['url'], '/patients/1?tab=payments')
        # Already-delivered alerts must work immediately, without waiting for the worker.
        from app.api.routes.payouts import notifications
        old_notice = self.db.query(Notification).first()
        old_notice.data = {'url': '/configuration/payment-alerts', 'payment_alert': True}
        self.db.commit()
        delivered = notifications(db=self.db, user=self.fixture.user)['data']['items']
        self.assertEqual(next(item for item in delivered if item['source'] == 'notification')['url'],
                         '/patients/1?tab=payments')
        bookings[0].paid_amount = 20; bookings[0].due_amount = 80
        self.db.commit()
        service.reconcile_rule(self.db, rule, self.now)
        notice = self.db.query(Notification).first()
        self.assertTrue(notice.data['resolved'])
        self.assertEqual(self.db.query(Notification).count(), 1)
        rule.config = {**rule.config, 'include_partial': True}
        self.db.commit()
        service.reconcile_rule(self.db, rule, self.now)
        self.assertFalse(notice.data['resolved'])
        self.assertIn('280.00', notice.message)
        self.assertEqual(self.db.query(PaymentAlertEvent).count(), 1)

    def test_receipt_details_match_summary_and_enforce_centre_access(self):
        self.db.add_all([PaymentModeMaster(id=1, payment_mode_name='Cash'), PaymentModeMaster(id=2, payment_mode_name='UPI')])
        for identifier, amount, stamp, patient in [(1, 100, datetime(2026, 10, 5, 6), 1),
                (2, 200, datetime(2026, 10, 10, 18, 29), 1),
                (3, 400, datetime(2026, 10, 10, 18, 30), 1),
                (4, 800, datetime(2026, 10, 5, 6), 2)]:
            self.db.add(Payment(id=identifier, patient_id=patient, payment_amount=amount, payment_mode=1 if identifier == 1 else 2, payment_date=stamp))
        self.db.commit()
        rule = self.rule('cash_summary')
        payload = next(iter(service.candidates(self.db, rule, datetime(2026, 10, 11, 6, tzinfo=timezone.utc)).values()))['payload']
        from urllib.parse import urlparse, parse_qs
        from app.services.payment_report_links import decode_report_token, report_token
        link = service.notification_data(rule, payload)['url']
        token = parse_qs(urlparse(link).query)['report'][0]
        self.assertNotIn('region_id=', link)
        self.assertNotIn('2026-10-', link)
        self.assertEqual(decode_report_token(token), (1, date(2026, 10, 1), date(2026, 10, 10)))
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_db] = lambda: self.db
        with TestClient(app) as client, patch.object(routes, '_require_user', return_value=self.fixture.user), \
                patch.object(routes, '_user_roles', return_value={'central_head'}), \
                patch.object(routes, '_user_region_ids', return_value=[1]):
            url = '/api/v1/ui/payment-alerts/receipts?region_id=1&start_date=2026-10-01&end_date=2026-10-10'
            response = client.get(url)
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()['data']
            self.assertEqual(data['total'], payload['amount'])
            self.assertEqual({row['mode'] for row in data['rows']}, {'Cash', 'UPI'})
            self.assertEqual(len(data['rows']), 2)
            encrypted = client.get('/api/v1/ui/payment-alerts/receipts', params={'report': token})
            self.assertEqual(encrypted.status_code, 200)
            self.assertEqual(encrypted.json()['data']['total'], payload['amount'])
            self.assertEqual(client.get('/api/v1/ui/payment-alerts/receipts', params={'report': token + 'broken'}).status_code, 422)
            other = report_token(2, date(2026, 10, 1), date(2026, 10, 10))
            self.assertEqual(client.get('/api/v1/ui/payment-alerts/receipts', params={'report': other}).status_code, 404)
            self.assertEqual(client.get(url.replace('region_id=1', 'region_id=2')).status_code, 404)
            self.assertEqual(client.get(url.replace('end_date=2026-10-10', 'end_date=2026-09-30')).status_code, 422)
    def test_cash_all_modes_ist_boundary_and_ten_day_schedule(self):
        self.db.add_all([PaymentModeMaster(id=1, payment_mode_name='Cash'), PaymentModeMaster(id=2, payment_mode_name='UPI')])
        for identifier, amount, stamp, patient in [(1, 100, datetime(2026, 10, 5, 6), 1),
                (2, 200, datetime(2026, 10, 10, 18, 29), 1),
                (3, 400, datetime(2026, 10, 10, 18, 30), 1),
                (4, 800, datetime(2026, 10, 5, 6), 2)]:
            self.db.add(Payment(id=identifier, patient_id=patient, payment_amount=amount, payment_mode=1 if identifier == 1 else 2, payment_date=stamp))
        self.db.commit()
        rule = self.rule('cash_summary')
        self.assertEqual(service.candidates(self.db, rule, datetime(2026, 10, 10, 6, tzinfo=timezone.utc)), {})
        now = datetime(2026, 10, 11, 6, tzinfo=timezone.utc)
        values = list(service.candidates(self.db, rule, now).values())
        self.assertEqual(values[0]['payload']['amount'], '300.00')
        service.reconcile_rule(self.db, rule, now)
        service.reconcile_rule(self.db, rule, now)
        self.assertEqual(self.db.query(Notification).count(), 1)

    def test_multi_therapist_sessions_count_once_and_other_centres_are_excluded(self):
        self.db.add_all([CrtProgramBooking(id=1, patient_id=1, program_id=2, slot_date=date(2026, 10, 10), due_amount=200),
            GroupProgramBooking(id=1, program_id=3, slot_id=1, slot_date=date(2026, 10, 10), region_id=1)])
        self.db.commit()
        self.fixture.booking(1, 2, crt_program_booking_id=1)
        self.fixture.booking(2, 2, therapist=2, crt_program_booking_id=1)
        primary = self.fixture.booking(3, 3, group_program_booking_id=1)
        primary.due_amount = 100
        self.fixture.booking(4, 3, therapist=2, group_program_booking_id=1)
        other = self.fixture.booking(5, 1, patient=2); other.due_amount = 500
        self.db.commit()
        rule = self.rule('unpaid_sessions')
        self.assertEqual(service.candidates(self.db, rule, self.now), {})
        rule.config = {**rule.config, 'session_count': 2}
        self.db.commit()
        values = list(service.candidates(self.db, rule, self.now).values())
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]['payload']['count'], 2)
        self.assertEqual(values[0]['payload']['amount'], '300.00')

    def test_fixed_first_session_package_start_and_payment_resolution(self):
        booking = self.fixture.booking(1, 1)
        booking.amount = 100; booking.due_amount = 100
        self.db.add_all([PatientProgramPricing(id=1, patient_id=1, program_id=1, billing_type='per_session', agreed_amount=100, effective_from=date(2026, 9, 1)),
            Package(id=1, name='General package', program_id=1, region_id=1, total_sessions=10, price=1000),
            PatientPackage(id=1, patient_id=1, package_id=1, start_date=date(2026, 10, 10), sessions_remaining=10,
                           total_amount=1000, due_amount=1000, paid_amount=0, status='active')])
        self.db.commit()
        rule = self.rule('unpaid_plans')
        before = datetime(2026, 10, 13, 6, tzinfo=timezone.utc)
        boundary = datetime(2026, 10, 14, 6, tzinfo=timezone.utc)
        self.assertEqual(service.candidates(self.db, rule, before), {})
        self.assertEqual(set(service.candidates(self.db, rule, boundary)), {'fixed:1', 'package:1'})
        service.reconcile_rule(self.db, rule, boundary)
        self.assertEqual(self.db.query(Notification).count(), 2)
        booking.due_amount = 0; booking.paid_amount = 100
        package = self.db.get(PatientPackage, 1); package.due_amount = 0; package.paid_amount = 1000
        self.db.commit()
        service.reconcile_rule(self.db, rule, boundary)
        self.assertTrue(all(row.data['resolved'] for row in self.db.query(Notification).all()))

    def test_configuration_defaults_permissions_and_preview_sends_nothing(self):
        from app.api.routes import routers
        app = FastAPI()
        for router in routers:
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        with TestClient(app) as client, patch.object(routes, '_require_user', return_value=self.fixture.user), \
             patch.object(routes, '_user_roles', return_value={'front_office'}), \
             patch.object(routes, '_user_region_ids', return_value=[1]), \
             patch.object(routes, 'eligible_users', return_value=[self.fixture.user]):
            base = '/api/v1/ui/payment-alerts'
            settings = client.get(base + '?region_id=1').json()['data']
            self.assertTrue(all(not rule['enabled'] for rule in settings['rules']))
            payload = settings['rules'][1]
            payload['enabled'] = True
            self.assertEqual(client.put(base + '/1', json=payload).status_code, 422)
            payload['recipients'] = [1]
            self.assertEqual(client.put(base + '/1', json=payload).status_code, 200)
            self.assertEqual(client.post(base + '/1/preview', json=payload).status_code, 200)
            self.assertEqual(self.db.query(Notification).count(), 0)
            self.assertEqual(client.get(base + '?region_id=2').status_code, 404)
            with patch.object(routes, '_user_roles', return_value={'therapist'}):
                self.assertEqual(client.get(base).status_code, 403)

    def test_worker_skips_disabled_rules_and_respects_another_workers_lease(self):
        from datetime import timedelta
        from sqlalchemy.orm import sessionmaker
        for identifier in (1, 2, 3):
            row = self.fixture.booking(identifier, 1); row.due_amount = 100
        rule = self.rule('unpaid_sessions')
        rule.enabled = False; self.db.commit()
        factory = sessionmaker(bind=self.fixture.engine)
        with patch.object(service, 'SessionLocal', side_effect=lambda: factory()):
            service.run_payment_alerts()
            self.assertEqual(self.db.query(Notification).count(), 0)
            rule.enabled = True
            lease = self.db.get(PaymentAlertLease, 'worker')
            self.db.refresh(lease)
            lease.owner = 'another-worker'; lease.expires_at = datetime.utcnow() + timedelta(minutes=10)
            self.db.commit()
            service.run_payment_alerts()
            self.assertEqual(self.db.query(Notification).count(), 0)
            lease.expires_at = datetime.utcnow() - timedelta(seconds=1); self.db.commit()
            service.run_payment_alerts()
            service.run_payment_alerts()
            self.assertEqual(self.db.query(Notification).count(), 1)

    def test_recipients_respect_centre_assignments_even_for_central_head(self):
        self.db.add_all([Role(id=1, name='central_head'), UserRole(id=1, user_id=1, role_id=1),
                         UserRegionMapping(id=1, userid=1, regionid=2)])
        self.db.commit()
        self.recipients.stop()
        try:
            self.assertEqual(service.eligible_users(self.db, 1), [])
            self.assertEqual([user.id for user in service.eligible_users(self.db, 2)], [1])
        finally:
            self.recipients.start()


if __name__ == '__main__':
    unittest.main()
