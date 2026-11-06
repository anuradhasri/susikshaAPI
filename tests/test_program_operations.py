import unittest
from datetime import date, datetime, time
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.models import (User, Region, Patient, Therapist, Program, SlotMaster, TherapyMaster,
    TherapistSlotMapping, PatientSlotBooking, PatientSlotBookingStatusMaster, PatientAssessmentStatusMaster,
    PatientAssessment, AssessmentMaster, CrtProgramBooking, GroupProgramBooking)
from app.models.payouts import TherapySheetAssignment
from app.api.routes import program_operations as routes
from app.services.program_operations import category_for


class ProgramOperationsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(id=1, username='report', email='report@example.test', hashed_password='unused', first_name='Report', last_name='User')
        self.db.add_all([self.user, Region(id=1, name='One', code='ONE'), Region(id=2, name='Two', code='TWO'),
            Patient(id=1, first_name='Child', last_name='One', date_of_birth=date(2020, 1, 1), region_id=1),
            Patient(id=2, first_name='Child', last_name='Two', date_of_birth=date(2020, 1, 1), region_id=2),
            Therapist(id=1, name='Therapist One', region_id=1), Therapist(id=2, name='Therapist Two', region_id=1),
            SlotMaster(id=1, slot_name='Morning', start_time=time(9), end_time=time(10), duration_minutes=60),
            TherapyMaster(id=1, name='Therapy', is_active=True)])
        for key, code in [(1, 'BOOKED'), (2, 'UNPAID_CANCELLED'), (3, 'COMPLETED'), (4, 'PAID_CANCELLED')]:
            self.db.add(PatientSlotBookingStatusMaster(id=key, code=code, name=code))
        self.db.add_all([PatientAssessmentStatusMaster(id=1, code='PENDING', name='Pending'),
                         PatientAssessmentStatusMaster(id=3, code='COMPLETED', name='Completed')])
        for identifier, name in enumerate(['General', 'Structured', 'Parent Training', 'Schooling', 'Vocational', 'Assessment'], 1):
            self.db.add(Program(id=identifier, program_name=name, region_id=1))
        self.db.commit()
        self.patches = [patch.object(routes, '_require_user', return_value=self.user),
                        patch.object(routes, '_user_region_ids', return_value=[1]),
                        patch.object(routes, '_user_roles', return_value={'front_office'}),
                        patch.object(routes, '_permission_shape', return_value={})]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.url = '/api/v1/ui/reports/program-operations?start_date=2026-10-01&end_date=2026-10-31'

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.db.close()
        self.engine.dispose()

    def booking(self, identifier, program, status=3, patient=1, therapist=1, **extra):
        mapping = TherapistSlotMapping(id=identifier, therapist_id=therapist, slot_id=1, therapy_id=1, slot_date=date(2026, 10, 10), status_id=1)
        row = PatientSlotBooking(id=identifier, patient_id=patient, program_id=program,
                                 therapist_slot_mapping_id=identifier, status_id=status, **extra)
        self.db.add_all([mapping, row])
        self.db.commit()
        return row

    def test_all_categories_deduplication_and_assessment_dates(self):
        self.db.add_all([CrtProgramBooking(id=1, patient_id=1, program_id=2, slot_date=date(2026, 10, 10)),
            GroupProgramBooking(id=1, program_id=3, region_id=1, slot_id=1, slot_date=date(2026, 10, 10)),
            AssessmentMaster(id=1, title='Language', assessment_name='Language assessment', region_id=1),
            PatientAssessment(id=1, patient_id=1, assessment_id=1, status_id=3,
                              created_date=datetime(2026, 9, 1), completed_date=datetime(2026, 10, 12))])
        self.db.commit()
        self.booking(1, 1)
        self.booking(2, 2, crt_program_booking_id=1)
        self.booking(3, 2, therapist=2, crt_program_booking_id=1)
        self.booking(4, 3, group_program_booking_id=1)
        self.booking(5, 3, therapist=2, group_program_booking_id=1)
        self.booking(6, 4, status=2)
        self.booking(7, 5, status=1)
        self.booking(8, 6)
        self.booking(9, 1, patient=2)  # another centre must not appear
        self.db.add_all([TherapySheetAssignment(id=1, patient_slot_booking_id=2, patient_id=1, therapist_id=1, status='pending'),
                         TherapySheetAssignment(id=2, patient_slot_booking_id=3, patient_id=1, therapist_id=2, status='submitted')])
        self.db.commit()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()['data']
        self.assertEqual(len(data['rows']), 7)  # six child sessions plus one assessment record
        self.assertEqual({row['patient_id'] for row in data['rows']}, {1})
        self.assertEqual({row['category'] for row in data['rows']}, {'General', 'Structured', 'Parent', 'Schooling', 'Vocational', 'Assessment'})
        structured = next(row for row in data['rows'] if row['category'] == 'Structured')
        self.assertEqual(structured['therapist_ids'], [1, 2])
        self.assertEqual(structured['pending_sheets'], 1)
        assessment = next(row for row in data['rows'] if row['activity'] == 'Assessment record')
        self.assertEqual(assessment['date'], '2026-10-12')
        self.assertEqual(sum(item['activities'] for item in data['summary']), 7)
        self.assertEqual(sum(item['cancelled'] for item in data['summary']), 1)

    def test_permissions_period_and_own_therapist_scope(self):
        self.assertEqual(self.client.get(self.url + '&region_id=2').status_code, 403)
        self.assertEqual(self.client.get(self.url.replace('2026-10-01', '2026-11-01')).status_code, 422)
        self.assertEqual(self.client.get(self.url.replace('2026-10-01', '2020-01-01')).status_code, 422)
        with patch.object(routes, '_user_roles', return_value=set()):
            self.assertEqual(self.client.get(self.url).status_code, 403)
        self.booking(1, 1, therapist=1)
        self.booking(2, 1, therapist=2)
        self.db.get(Therapist, 1).user_id = 1
        self.db.commit()
        with patch.object(routes, '_user_roles', return_value={'therapist'}), patch.object(routes, '_permission_shape', return_value={'menu.reports': {'view': True}}):
            data = self.client.get(self.url).json()['data']
            self.assertTrue(data['therapist_scoped'])
            self.assertEqual(len(data['rows']), 1)
            self.assertEqual(data['rows'][0]['therapist_ids'], [1])

    def test_classification_retains_unrecognized_programs(self):
        self.assertEqual(category_for('CRT Program'), 'Structured')
        self.assertEqual(category_for('PEG'), 'Parent')
        self.assertEqual(category_for('Social program'), 'Other')
        self.assertEqual(category_for('Assessment'), 'Assessment')


if __name__ == '__main__':
    unittest.main()
