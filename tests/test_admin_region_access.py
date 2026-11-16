import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.models import Region, Role, User, UserRole, UserRegionMapping
from app.repositories.user_repository import UserRepository
from app.dependencies.auth import _user_region_ids
from app.api.routes.ui import _user_region_ids as ui_region_ids


class AdminRegionAccessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(id=1, username='admin-test', email='admin@example.test',
                         first_name='Admin', last_name='Test', hashed_password='unused')
        self.db.add_all([self.user, Role(id=1, name='admin'),
                         Region(id=1, name='Mulund', code='M'),
                         Region(id=2, name='Andheri', code='A'),
                         Region(id=3, name='Deleted', code='D', deleted_at=datetime.now())])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def assert_regions(self, expected):
        self.assertEqual(UserRepository.region_ids(self.db, 1), expected)
        self.assertEqual(_user_region_ids(self.db, 1), expected)
        self.assertEqual(ui_region_ids(self.db, self.user), expected)

    def test_admin_includes_all_current_and_future_centres_without_mappings(self):
        self.db.add(UserRole(user_id=1, role_id=1))
        self.db.commit()
        self.assert_regions([1, 2])
        self.db.add(Region(id=4, name='New centre', code='NEW'))
        self.db.commit()
        self.assert_regions([1, 2, 4])
        self.assertEqual(self.db.query(UserRegionMapping).count(), 0)

    def test_admin_mapping_does_not_restrict_access(self):
        self.db.add_all([UserRole(user_id=1, role_id=1), UserRegionMapping(userid=1, regionid=2)])
        self.db.commit()
        self.assert_regions([1, 2])

    def test_staff_and_revoked_admin_only_receive_assigned_centres(self):
        mapping = UserRole(user_id=1, role_id=1, deleted_at=datetime.now())
        self.db.add_all([mapping, UserRegionMapping(userid=1, regionid=2)])
        self.db.commit()
        self.assert_regions([2])

    def test_unmapped_non_admin_has_no_regions(self):
        self.assert_regions([])
