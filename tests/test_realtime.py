"""Live scope, transaction and WebSocket regression checks (no production database)."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text, Column, ForeignKey, Integer, Float, String, create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from app.services.realtime import LivePrincipal, RealtimeHub, visible_event
from app.services.realtime_events import install_realtime_events

Base = declarative_base()
class CentrePatient(Base):
    __tablename__ = 'patients'
    id = Column(Integer, primary_key=True)
    region_id = Column(Integer)
    name = Column(String)
class Receipt(Base):
    __tablename__ = 'payments'
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    amount = Column(Float)
class Notice(Base):
    __tablename__ = 'notifications'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    title = Column(String)
class Recorder:
    def __init__(self): self.events = []
    def publish_threadsafe(self, event): self.events.append(event)
recorder = Recorder()
class TestSession(Session): pass
install_realtime_events(recorder, Base.metadata, TestSession)


def principal(user_id=1, regions=(1,), topics=('payouts', 'payments', 'appointments', 'notifications', 'access')):
    return LivePrincipal(user_id, frozenset(regions), frozenset(topics), time.time() + 3600)


class ScopeTests(unittest.TestCase):
    def test_foreign_centre_is_not_delivered(self):
        self.assertIsNone(visible_event(principal(), {'id': '1', 'topics': ['payouts'], 'region_ids': [2]}))
    def test_private_notifications_only_reach_recipient(self):
        event = {'id': '1', 'topics': ['notifications'], 'user_ids': [2]}
        self.assertIsNone(visible_event(principal(), event))
        self.assertIsNotNone(visible_event(principal(user_id=2), event))
    def test_payload_exposes_only_authorized_invalidation_metadata(self):
        message = visible_event(principal(), {'id': '1', 'topics': ['payouts', 'secret'], 'region_ids': [1, 2], 'amount': 10000, 'token': 'secret'})
        self.assertEqual(message['topics'], ['payouts'])
        self.assertEqual(message['region_ids'], [1])
        self.assertNotIn('amount', message)
        self.assertNotIn('token', message)
    def test_unknown_scope_fails_closed(self):
        self.assertIsNone(visible_event(principal(), {'id': '1', 'topics': ['payouts']}))


class CommitTests(unittest.TestCase):
    def setUp(self):
        recorder.events.clear()
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, class_=TestSession)()
        self.db.add(CentrePatient(id=1, region_id=3, name='Test'))
        self.db.commit()
        recorder.events.clear()
    def tearDown(self):
        self.db.close(); self.engine.dispose()
    def test_not_published_until_commit_and_resolves_patient_centre(self):
        self.db.add(Receipt(patient_id=1, amount=25))
        self.db.flush()
        self.assertEqual(recorder.events, [])
        self.db.commit()
        self.assertEqual(recorder.events[0]['region_ids'], [3])
        self.assertIn('payouts', recorder.events[0]['topics'])
    def test_new_parent_and_payment_in_same_commit_resolve_the_centre(self):
        self.db.add(CentrePatient(id=2, region_id=5, name='New'))
        self.db.add(Receipt(patient_id=2, amount=25)); self.db.commit()
        self.assertTrue(any('payouts' in item['topics'] and item['region_ids'] == [5] for item in recorder.events))

    def test_rollback_emits_nothing(self):
        self.db.add(Receipt(patient_id=1, amount=25)); self.db.flush(); self.db.rollback()
        self.assertEqual(recorder.events, [])
    def test_noop_does_not_emit(self):
        row = self.db.get(CentrePatient, 1); row.name = 'Test'; self.db.commit()
        self.assertEqual(recorder.events, [])
    def test_bulk_update_emits_after_commit(self):
        self.db.query(CentrePatient).filter_by(id=1).update({'name': 'Updated'})
        self.assertEqual(recorder.events, []); self.db.commit()
        self.assertEqual(recorder.events[0]['region_ids'], [3])
    def test_raw_sql_insert_update_and_rollback(self):
        self.db.execute(text('INSERT INTO payments (patient_id, amount) VALUES (:patient_id, :amount)'), {'patient_id': 1, 'amount': 30})
        self.db.commit()
        self.assertIn('payouts', recorder.events[0]['topics'])
        recorder.events.clear()
        self.db.execute(text('UPDATE payments SET amount=:amount WHERE patient_id=:patient_id'), {'patient_id': 1, 'amount': 45})
        self.db.rollback()
        self.assertEqual(recorder.events, [])
        self.db.execute(text('UPDATE payments SET amount=:amount WHERE patient_id=:patient_id'), {'patient_id': 1, 'amount': 45})
        self.db.commit()
        self.assertEqual(recorder.events[0]['region_ids'], [3])

    def test_notifications_target_the_recipient_and_preserve_origin(self):
        self.db.info.update(live_origin='browser-one', live_actor_id=7)
        self.db.add(Notice(user_id=8, title='New')); self.db.commit()
        self.assertEqual(recorder.events[0]['user_ids'], [8])
        self.assertEqual(recorder.events[0]['origin'], 'browser-one')
    def test_nested_commit_waits_for_outer_commit(self):
        with self.db.begin_nested(): self.db.add(Receipt(patient_id=1, amount=25))
        self.assertEqual(recorder.events, []); self.db.commit()
        self.assertTrue(recorder.events)
    def test_savepoint_rollback_preserves_outer_events_only(self):
        row = self.db.get(CentrePatient, 1); row.name = 'Outer'; self.db.flush()
        try:
            with self.db.begin_nested():
                self.db.add(Receipt(patient_id=1, amount=25)); self.db.flush(); raise ValueError('rollback')
        except ValueError: pass
        self.db.commit()
        self.assertEqual(len(recorder.events), 1)
        self.assertNotIn('payouts', recorder.events[0]['topics'])


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): self.hub = RealtimeHub(); await self.hub.start()
    async def asyncTearDown(self): await self.hub.stop()
    async def test_other_tab_gets_event_but_origin_does_not(self):
        own = self.hub.connect(principal(), 'browser-one')
        other = self.hub.connect(principal(), 'browser-two')
        await self.hub.publish({'topics': ['payouts'], 'region_ids': [1], 'origin': 'browser-one', 'actor_id': 1})
        self.assertTrue(own.queue.empty()); self.assertEqual((await other.queue.get())['type'], 'invalidate')
    async def test_own_access_changes_are_not_suppressed(self):
        client = self.hub.connect(principal(), 'browser-one')
        await self.hub.publish({'topics': ['access'], 'user_ids': [1], 'origin': 'browser-one', 'actor_id': 1})
        self.assertEqual((await client.queue.get())['topics'], ['access'])

    async def test_slow_client_is_told_to_reconnect(self):
        client = self.hub.connect(principal(), 'browser-one')
        for _ in range(65): await self.hub.publish({'topics': ['payouts'], 'region_ids': [1]})
        self.assertEqual((await client.queue.get())['type'], 'close')
    async def test_expired_connection_is_closed(self):
        p = principal(); p.expires_at = time.time() - 1
        client = self.hub.connect(p, 'browser-one')
        await self.hub.publish({'topics': ['payouts'], 'region_ids': [1]})
        self.assertEqual((await client.queue.get())['type'], 'unauthorized')


class LocalHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_updates_work_when_redis_import_is_unavailable(self):
        import builtins
        original_import = builtins.__import__
        def without_redis(name, *args, **kwargs):
            if name == 'redis' or name.startswith('redis.'):
                raise ImportError('Redis is not installed')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=without_redis):
            hub = RealtimeHub()
            await hub.start()
            try:
                clients = [hub.connect(principal(), name) for name in ('first', 'second')]
                await hub.publish({'topics': ['payments'], 'region_ids': [1]})
                for client in clients:
                    event = await asyncio.wait_for(client.queue.get(), 1)
                    self.assertEqual(event['topics'], ['payments'])
                    self.assertTrue(client.queue.empty())
            finally:
                await hub.stop()
            self.assertEqual(len(hub.connections), 0)


class AuthenticationTests(unittest.TestCase):
    def authenticate(self, region, roles=('front_office',), regions=(1, 2), payload=None, active=True):
        from app.api.routes import live
        db = MagicMock(); db.scalar.return_value = SimpleNamespace(id=7, is_active=active)
        db.scalars.side_effect = [list(roles), list(regions)]
        context = MagicMock(); context.__enter__.return_value = db
        with patch.object(live, 'decode_token', return_value=payload or {'user_id': 7, 'exp': time.time() + 3600}), patch.object(live, 'SessionLocal', return_value=context):
            return live.authenticate_live('signed-access-token', region)
    def test_subscription_is_limited_to_assigned_centres(self):
        self.assertEqual(self.authenticate(1).region_ids, frozenset({1}))
        with self.assertRaises(PermissionError): self.authenticate(3)
    def test_admin_can_subscribe_to_all_centres(self):
        self.assertTrue(self.authenticate(None, roles=('admin',), regions=()).all_regions)
    def test_therapists_do_not_receive_admin_topics(self):
        self.assertNotIn('payment-alerts', self.authenticate(None, roles=('therapist',)).topics)
    def test_refresh_tokens_expired_tokens_and_inactive_users_are_denied(self):
        with self.assertRaises(ValueError): self.authenticate(None, payload={'user_id': 7, 'exp': time.time() + 60, 'type': 'refresh'})
        with self.assertRaises(ValueError): self.authenticate(None, payload={'user_id': 7, 'exp': time.time() - 1})
        with self.assertRaises(ValueError): self.authenticate(None, active=False)


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        from app.api.routes import live
        self.live = live
        self.hub = RealtimeHub()
        @asynccontextmanager
        async def lifespan(app):
            await self.hub.start(); yield; await self.hub.stop()
        self.app = FastAPI(lifespan=lifespan); self.app.include_router(live.router)
        self.patch_hub = patch.object(live, 'hub', self.hub); self.patch_hub.start()
        self.patch_auth = patch.object(live, 'authenticate_live', return_value=principal()); self.patch_auth.start()
    def tearDown(self): self.patch_auth.stop(); self.patch_hub.stop()
    def test_authenticate_heartbeat_and_disconnect_cleanup(self):
        with TestClient(self.app) as client:
            with client.websocket_connect('/api/v1/live', headers={'origin': 'http://localhost:5173'}) as ws:
                ws.send_json({'type': 'authenticate', 'token': 'test', 'client_id': 'browser-one'})
                self.assertEqual(ws.receive_json()['type'], 'ready')
                self.hub.publish_threadsafe({'topics': ['payouts'], 'region_ids': [1]})
                self.assertEqual(ws.receive_json()['type'], 'invalidate')
                ws.send_json({'type': 'pong'})
        self.assertEqual(len(self.hub.connections), 0)
    def test_rest_commit_reaches_another_browser_through_the_socket(self):
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        class LiveTestSession(Session): pass
        sessions = sessionmaker(bind=engine, class_=LiveTestSession)
        install_realtime_events(self.hub, Base.metadata, LiveTestSession)
        with sessions() as db:
            db.add(CentrePatient(id=1, region_id=1, name='Test')); db.commit()
        @self.app.post('/test-change')
        def change():
            with sessions() as db:
                db.info.update(live_origin='saving-browser', live_actor_id=1)
                db.add(Receipt(patient_id=1, amount=25)); db.commit()
            return {'saved': True}
        try:
            with TestClient(self.app) as client, client.websocket_connect('/api/v1/live') as ws:
                ws.send_json({'type': 'authenticate', 'token': 'test', 'client_id': 'viewing-browser'})
                self.assertEqual(ws.receive_json()['type'], 'ready')
                self.assertTrue(client.post('/test-change').json()['saved'])
                event = ws.receive_json()
                self.assertEqual(event['type'], 'invalidate')
                self.assertIn('payouts', event['topics'])
                self.assertNotIn('amount', event)
        finally: engine.dispose()

    def test_missing_auth_is_rejected(self):
        from starlette.websockets import WebSocketDisconnect
        with TestClient(self.app) as client:
            with client.websocket_connect('/api/v1/live') as ws:
                ws.send_json({'type': 'subscribe'})
                with self.assertRaises(WebSocketDisconnect) as error: ws.receive_json()
                self.assertEqual(error.exception.code, 4401)
    def test_untrusted_origin_is_rejected(self):
        from starlette.websockets import WebSocketDisconnect
        with TestClient(self.app) as client, self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect('/api/v1/live', headers={'origin': 'https://untrusted.invalid'}): pass
    def test_region_authorization_failure_is_rejected(self):
        from starlette.websockets import WebSocketDisconnect
        with patch.object(self.live, 'authenticate_live', side_effect=PermissionError('not allowed')):
            with TestClient(self.app) as client, client.websocket_connect('/api/v1/live') as ws:
                ws.send_json({'type': 'authenticate', 'token': 'test', 'client_id': 'browser-one', 'region_id': 999})
                with self.assertRaises(WebSocketDisconnect) as error: ws.receive_json()
                self.assertEqual(error.exception.code, 4403)


if __name__ == '__main__': unittest.main()
