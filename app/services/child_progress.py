def consolidate_progress(evaluations):
    """Use the newest assessment containing each skill, including NO ratings.

    Evaluation date wins; edits and then record ID break same-date ties.
    A newer NO must replace an older E/A rather than resurrect old progress.
    """
    ordered = sorted(evaluations, key=lambda row: (
        row.evaluation_date, str(row.updated_at or ''), row.id), reverse=True)
    latest = {}
    for row in ordered:
        for response in row.responses:
            if response.skill_id not in latest:
                latest[response.skill_id] = {
                    'skill_id': response.skill_id, 'status': response.status,
                    'comments': response.comments or '', 'evaluation_date': row.evaluation_date,
                    'summary_id': row.id,
                }
    return list(latest.values())
