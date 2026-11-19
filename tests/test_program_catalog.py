import unittest
from datetime import date, time
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import programs, ui
from app.core.database import Base, get_db
from app.models.models import Package, PackageProgramAssignment, Patient, PatientPackage, Program, Region, SlotMaster
from app.schemas.schemas import SlotBookingCreate
from app.services.appointment_service import AppointmentService


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
            self.assertEqual(client.post(path, json=payload).status_code, 422)
            legacy = Package(region_id=1, program_id=1, name='Shared 12', total_sessions=12,
                             price=13200, duration_days=30,
                             program_assignments=[PackageProgramAssignment(program_id=2)])
            db.add(legacy)
            db.commit()
            package_id = legacy.id
            duplicate = client.post(path, json={**payload, 'name': 'Second package', 'program_ids': [2]})
            self.assertEqual(duplicate.status_code, 422)
            self.assertIn('already has a package', duplicate.json()['detail'])
            self.assertEqual(client.put(f'{path}/{package_id}', json=payload).status_code, 422)
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

            revised_id = result.json()['data']['id']
            result = client.put(f'{path}/{revised_id}', json={**payload, 'amount': '14000', 'program_ids': []})
            self.assertEqual(result.status_code, 200, result.text)
            unassigned_id = result.json()['data']['id']
            option = client.get(path).json()['data'][0]
            self.assertEqual(option['id'], unassigned_id)
            self.assertEqual(option['program_ids'], [])
            self.assertIsNone(db.get(Package, unassigned_id).program_id)
            self.assertEqual(programs.assigned_ids(db.get(Package, revised_id)), [2])
            self.assertIsNone(AppointmentService._program_package_options(db, db.get(Program, 2)).first())
            result = client.put(f'{path}/{unassigned_id}', json={**payload, 'amount': '14000', 'program_ids': [2]})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(client.get(path).json()['data'][0]['program_ids'], [2])
            reserved = client.put(f'{path}/{unassigned_id}', json={**payload, 'amount': '14000', 'program_ids': [1, 2]})
            self.assertEqual(reserved.status_code, 422)
            reused = client.post('/api/v1/ui/program-catalog/1', json={
                'name': 'New Program', 'duration_minutes': 45, 'session_type': 'individual',
                'package_ids': [unassigned_id],
            })
            self.assertEqual(reused.status_code, 422)
            expiry = date(2026, 12, 18)
            db.add(Patient(id=1, first_name='Test', last_name='Child', date_of_birth=date(2020, 1, 1), region_id=1))
            purchase = PatientPackage(patient_id=1, package_id=unassigned_id, start_date=date(2026, 11, 18),
                                      end_date=expiry, sessions_remaining=12, total_amount=14000)
            db.add(purchase)
            db.commit()
            self.assertTrue(client.get(path).json()['data'][0]['terms_locked'])
            saved = {**payload, 'amount': '14000', 'program_ids': [2]}
            unassign = client.put(f'{path}/{unassigned_id}', json={**saved, 'program_ids': []})
            self.assertEqual(unassign.status_code, 422)
            self.assertIn('in use', unassign.json()['detail'])
            replacement = client.put('/api/v1/ui/program-catalog/1/2', json={
                'name': 'CRT', 'session_type': 'individual', 'duration_minutes': 120,
                'new_packages': [{'name': 'Replacement', 'sessions': 12, 'amount': '14000'}],
            })
            self.assertEqual(replacement.status_code, 422)
            self.assertIn('in use', replacement.json()['detail'])

            for change in ({'amount': '15000'}, {'sessions': 13}):
                self.assertEqual(client.put(f'{path}/{unassigned_id}', json={**saved, **change}).status_code, 422)
            validity = client.put(f'{path}/{unassigned_id}', json={**saved, 'duration_days': 45})
            self.assertEqual(validity.status_code, 200, validity.text)
            self.assertEqual(validity.json()['data']['id'], unassigned_id)
            self.assertEqual(db.get(Package, unassigned_id).duration_days, 45)
            self.assertEqual(purchase.end_date, expiry)



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
            package = {'name': 'Four sessions', 'sessions': 4, 'amount': '3000'}
            self.assertEqual(client.post(path, json={**data, 'new_packages': [package, package]}).status_code, 422)
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


    def test_custom_package_booking_purchases_once_and_uses_session_rate(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        db = sessionmaker(bind=engine, autoflush=False)()
        self.addCleanup(db.close)
        db.add(Region(id=1, name='One', code='ONE'))
        db.add_all([
            Program(id=1, region_id=1, program_name='Play Club'),
            Program(id=2, region_id=1, program_name='General', per_session_amount=1200),
            Patient(id=1, first_name='Test', last_name='Child', date_of_birth=date(2020, 1, 1), region_id=1),
            SlotMaster(id=1, slot_name='Morning', start_time=time(9), end_time=time(9, 45), duration_minutes=45),
            Package(id=1, region_id=1, program_id=2, name='Shared 8', total_sessions=8, price=9600,
                    program_assignments=[PackageProgramAssignment(program_id=1)]),
        ])
        db.commit()

        booking = SlotBookingCreate(patient_id=1, therapist_id=1, therapy_id=1, program_id=1, slot_id=1,
                                    slot_date=date(2026, 11, 18), region_id=1, use_package=False)
        with patch.object(AppointmentService, '_ensure_package_billing_schema'), \
                patch.object(AppointmentService, '_therapist_unavailable_for_slot', return_value=False), \
                patch.object(AppointmentService, '_validate_therapist_window_available'), \
                patch.object(AppointmentService, '_validate_patient_window_available'):
            first = AppointmentService.book_slot(db, booking)['patient_slot_booking']
            second = AppointmentService.book_slot(
                db, booking.model_copy(update={'slot_date': date(2026, 11, 19)})
            )['patient_slot_booking']

        purchase = db.query(PatientPackage).one()
        self.assertEqual(purchase.package_id, 1)
        self.assertEqual(purchase.total_amount, 9600)
        self.assertEqual(purchase.sessions_remaining, 8)
        for session in (first, second):
            self.assertTrue(session.is_package_session)
            self.assertEqual(session.patient_package_id, purchase.id)
            self.assertEqual(session.amount, 1200)
            self.assertEqual(session.due_amount, 1200)


    def test_custom_program_books_multiple_children_and_therapists_with_one_charge_per_child(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        db = sessionmaker(bind=engine, autoflush=False)()
        self.addCleanup(db.close)
        db.add_all([
            Region(id=1, name='One', code='ONE'),
            Program(id=1, region_id=1, program_name='Play Club', per_session_amount=1200, session_type='group', capacity=1),
            Patient(id=1, first_name='First', last_name='Child', date_of_birth=date(2020, 1, 1), region_id=1),
            Patient(id=2, first_name='Second', last_name='Child', date_of_birth=date(2020, 1, 1), region_id=1),
            Patient(id=3, first_name='Third', last_name='Child', date_of_birth=date(2020, 1, 1), region_id=1),
            SlotMaster(id=1, slot_name='Morning', start_time=time(9), end_time=time(9, 45), duration_minutes=45),
        ])
        db.commit()

        bookings = []
        with patch.object(AppointmentService, '_therapist_unavailable_for_slot', return_value=False):
            for therapist_id in (1, 2):
                for child_id in (1, 2, 3):
                    request = SlotBookingCreate(
                        patient_id=child_id, therapist_id=therapist_id, therapy_id=1, program_id=1,
                        slot_id=1, slot_date=date(2026, 11, 18), region_id=1, use_package=False,
                        allow_shared_slot=True, is_primary=therapist_id == 1,
                    )
                    bookings.append(AppointmentService.book_slot(db, request, commit=False)['patient_slot_booking'])
        db.commit()

        self.assertEqual(len({row.id for row in bookings}), 6)
        group_ids = {row.group_program_booking_id for row in bookings}
        self.assertEqual(len(group_ids), 1)
        self.assertNotIn(None, group_ids)
        self.assertEqual([row.amount for row in bookings], [1200, 1200, 1200, 0, 0, 0])
