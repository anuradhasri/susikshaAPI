import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import programs, ui
from app.core.database import Base, get_db
from app.models.models import Package, Region


class ProgramCatalogTests(unittest.TestCase):
    def test_purchase_options_include_custom_packages_and_respect_centre(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        db = sessionmaker(bind=engine)()
        self.addCleanup(db.close)
        db.add_all([Region(id=1, name='One', code='ONE'), Region(id=2, name='Two', code='TWO')])
        db.flush()
        db.add_all([
            Package(id=1, region_id=1, name='CRT 5 Session - Package', total_sessions=5, price=45000, is_active=True),
            Package(id=2, region_id=1, name='CRT 10 Session Package', total_sessions=10, price=90000, is_active=True),
            Package(id=3, region_id=1, name='Old package', total_sessions=10, price=80000, is_active=False),
            Package(id=4, region_id=2, name='Other centre', total_sessions=10, price=90000, is_active=True),
        ])
        db.commit()
        with patch.object(ui, '_ensure_package_master_schema'), \
                patch.object(ui, '_user_region_ids', return_value=[1]), \
                patch.object(ui, '_user_roles', return_value={'admin'}):
            self.assertEqual({row.id for row in ui._base_query('packages', db, None).all()}, {1, 2})

    def test_shared_package_assignments_purchase_and_booking(self):
        from app.models.models import Program, PatientPackage
        from app.services.appointment_service import AppointmentService
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        db = sessionmaker(bind=engine)()
        self.addCleanup(db.close)
        db.add_all([Region(id=1, name='One', code='ONE'), Region(id=2, name='Two', code='TWO')])
        db.flush()
        db.add_all([Program(id=1, region_id=1, program_name='General'),
                    Program(id=2, region_id=1, program_name='CRT'),
                    Program(id=3, region_id=2, program_name='Other'),
                    Program(id=4, region_id=1, program_name='Parent Training Program: PEG')])
        db.commit()
        app = FastAPI()
        app.include_router(programs.router)
        app.dependency_overrides[get_db] = lambda: db
        path = '/api/v1/ui/program-catalog/1/packages'
        payload = {'name': 'Shared 12', 'sessions': 12, 'amount': '13200', 'duration_days': 30, 'program_ids': [1, 2]}
        with TestClient(app) as client, patch.object(programs, '_require_user', return_value=object()), \
                patch.object(programs, '_user_roles', return_value={'front_office'}), \
                patch.object(programs, '_user_region_ids', return_value=[1]):
            self.assertEqual(client.post(path, json={**payload, 'program_ids': [1, 3]}).status_code, 422)
            self.assertEqual(client.post(path, json={**payload, 'program_ids': [4]}).status_code, 422)
            result = client.post(path, json=payload)
            self.assertEqual(result.status_code, 200, result.text)
            package_id = result.json()['data']['id']
            self.assertEqual(client.put(f'{path}/{package_id}', json=payload).status_code, 200)
            options = client.get(path).json()['data']
            self.assertEqual(options[0]['program_ids'], [1, 2])
            row = db.get(Package, package_id)
            self.assertEqual(ui._master_package_shape(row)['program_ids'], [1, 2])
            from types import SimpleNamespace
            purchase = SimpleNamespace(package=row)
            self.assertTrue(AppointmentService._package_matches_program(purchase, db.get(Program, 2), 'crt'))
            self.assertFalse(AppointmentService._package_matches_program(purchase, db.get(Program, 3), 'general'))
            # Program edits preserve assignments and package pricing.
            result = client.put('/api/v1/ui/program-catalog/1/1', json={'name': 'General', 'duration_minutes': 50, 'session_type': 'individual', 'session_amount': '1200'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(db.get(Package, package_id).is_active)
            result = client.put(f'{path}/{package_id}', json={**payload, 'program_ids': [2]})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(client.get(path).json()['data'][0]['program_ids'], [2])
            result = client.put(f'{path}/{package_id}', json={**payload, 'amount': '14000', 'program_ids': [2]})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertNotEqual(result.json()['data']['id'], package_id)
            self.assertFalse(db.get(Package, package_id).is_active)
            self.assertEqual(db.get(Package, package_id).price, 13200)

    def test_centre_scope_pricing_validation_and_package_revision(self):
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        db = sessionmaker(bind=engine)()
        self.addCleanup(db.close)
        db.add_all([Region(id=1, name='One', code='ONE'), Region(id=2, name='Two', code='TWO')])
        db.commit()
        app = FastAPI()
        app.include_router(programs.router)
        app.dependency_overrides[get_db] = lambda: db
        path = '/api/v1/ui/program-catalog/1'
        data = {'name': 'Language Club', 'duration_minutes': 35, 'session_type': 'group', 'capacity': 4}
        with TestClient(app) as client, patch.object(programs, '_require_user', return_value=object()), \
                patch.object(programs, '_user_roles', return_value={'front_office'}), \
                patch.object(programs, '_user_region_ids', return_value=[1]):
            self.assertEqual(client.get(path[:-1] + '2').status_code, 403)
            self.assertEqual(client.post(path, json={**data, 'packages': [{'sessions': 4}]}).status_code, 422)
            result = client.post(path, json={**data, 'session_amount': '850'})
            self.assertEqual(result.status_code, 200, result.text)
            program = result.json()['data']
            self.assertEqual(program['packages'], [])
            self.assertEqual(program['capacity'], 4)
            self.assertEqual(db.query(Package).count(), 0)
            edit_path = f"{path}/{program['id']}"
            result = client.put(edit_path, json={**data, 'packages': [{'sessions': 4, 'amount': '3000'}]})
            self.assertEqual(result.status_code, 200, result.text)
            option = result.json()['data']['packages'][0]
            result = client.put(edit_path, json={**data, 'packages': [{**option, 'amount': '3200'}]})
            self.assertEqual(result.status_code, 200, result.text)
            replacement = result.json()['data']['packages'][0]
            self.assertNotEqual(replacement['id'], option['id'])
            previous = db.get(Package, option['id'])
            self.assertEqual(previous.price, 3000)
            self.assertFalse(previous.is_active)
            self.assertEqual(client.get(path).json()['data'][0]['packages'], [replacement])
