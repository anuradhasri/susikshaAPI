import unittest
from datetime import date
from types import SimpleNamespace as Row

from app.services.child_progress import consolidate_progress


def evaluation(identifier, day, answers, updated='2026-10-30T10:00:00'):
    return Row(id=identifier, evaluation_date=date.fromisoformat(day), updated_at=updated,
               responses=[Row(skill_id=skill, status=status, comments=f'note {identifier}')
                          for skill, status in answers])


class ChildProgressTests(unittest.TestCase):
    def test_latest_rating_per_skill_keeps_other_levels_and_partial_answers(self):
        result = consolidate_progress([
            evaluation(1, '2026-10-01', [(11, 'E'), (22, 'A'), (33, 'E')]),
            evaluation(2, '2026-10-20', [(11, 'A'), (22, 'NO')]),
        ])
        by_skill = {item['skill_id']: item for item in result}
        self.assertEqual({key: value['status'] for key, value in by_skill.items()},
                         {11: 'A', 22: 'NO', 33: 'E'})
        self.assertEqual(by_skill[11]['summary_id'], 2)
        self.assertEqual(by_skill[11]['comments'], 'note 2')

    def test_assessment_date_wins_over_edit_date_and_input_order(self):
        rows = [evaluation(1, '2026-10-01', [(1, 'A')], '2026-10-30'),
                evaluation(2, '2026-10-02', [(1, 'E')], '2026-10-03')]
        self.assertEqual(consolidate_progress(rows)[0]['status'], 'E')
        self.assertEqual(consolidate_progress(rows), consolidate_progress(list(reversed(rows))))

    def test_same_day_prefers_last_edit_then_id(self):
        rows = [evaluation(1, '2026-10-01', [(1, 'E')], '2026-10-02'),
                evaluation(2, '2026-10-01', [(1, 'A')], '2026-10-03'),
                evaluation(3, '2026-10-01', [(1, 'NO')], '2026-10-03')]
        self.assertEqual(consolidate_progress(rows)[0]['summary_id'], 3)

    def test_empty_history(self):
        self.assertEqual(consolidate_progress([]), [])


if __name__ == '__main__':
    unittest.main()
