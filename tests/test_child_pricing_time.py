import unittest
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock
from app.services.child_pricing import session_pricing, pricing_starts_at

class PricingTimeTests(unittest.TestCase):
    def test_earlier_session_keeps_standard_and_later_sessions_use_new_rate(self):
        # Created at 13:30 India time, between the 12:30 and 14:00 sessions.
        price = SimpleNamespace(effective_from=date(2026, 10, 29), created_at=datetime(2026, 10, 29, 13, 30), agreed_amount=1000)
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [price]
        # Scheduled time is deliberately the same: actual completion decides.
        amounts = [session_pricing(db, 1, 1, date(2026, 10, 29), time(12,30), completed_at=completed)
                   for completed in [datetime(2026,10,29,7,59), datetime(2026,10,29,8,1), datetime(2026,10,29,9)]]
        self.assertEqual([p.agreed_amount if p else 1200 for p in amounts], [1200,1000,1000])
        self.assertEqual(sum(p.agreed_amount if p else 1200 for p in amounts), 3200)
        self.assertIsNone(session_pricing(db, 1, 1, date(2026,10,29), time(14), completed_at=datetime(2026,10,29,8)))
        self.assertIsNone(session_pricing(db, 1, 1, date(2026,10,29), time(14)))
        self.assertIs(session_pricing(db, 1, 1, date(2026,10,30), time(0)), price)
        # Later edits to booking/payment metadata cannot move the cutoff.
        self.assertIsNone(session_pricing(db, 1, 1, date(2026,10,29), time(15), completed_at=datetime(2026,10,29,7)))

    def test_explicit_different_effective_date_starts_at_midnight(self):
        price = SimpleNamespace(effective_from=date(2026,10,30), created_at=datetime(2026,10,29,8))
        self.assertEqual(pricing_starts_at(price), datetime(2026,10,30))
