"""Read-only operational reporting helpers; no billing or payout synchronization."""
from collections import defaultdict

CATEGORIES = ['General', 'Structured', 'Parent', 'Schooling', 'Vocational', 'Assessment', 'Other']


def category_for(name):
    name = (name or '').lower()
    if 'assessment' in name:
        return 'Assessment'
    if 'crt' in name or 'structured' in name:
        return 'Structured'
    if 'vocational' in name:
        return 'Vocational'
    if any(word in name for word in ('parent', 'peg', 'prewriting', 'prewritting', 'toilet')):
        return 'Parent'
    if 'school' in name:
        return 'Schooling'
    if not name or 'general' in name:
        return 'General'
    return 'Other'


def booking_status(codes, day, today):
    codes = [str(code or '').upper() for code in codes]
    if all(code == 'COMPLETED' for code in codes):
        return 'Completed'
    if all('CANCEL' in code for code in codes):
        return 'Cancelled'
    if any(code == 'COMPLETED' or 'CANCEL' in code for code in codes):
        return 'In progress'
    return 'Pending' if day < today else 'Booked'


def summarize_operations(rows):
    groups = []
    for category in CATEGORIES:
        items = [row for row in rows if row['category'] == category]
        groups.append({
            'category': category, 'activities': len(items),
            'sessions': sum(row['activity'] == 'Session' for row in items),
            'assessments': sum(row['activity'] == 'Assessment record' for row in items),
            'children': len({row['patient_id'] for row in items if row['patient_id']}),
            **{key: sum(row['status'] in statuses for row in items) for key, statuses in {
                'completed': {'Completed'}, 'cancelled': {'Cancelled'},
                'booked': {'Booked'}, 'pending': {'Pending', 'In progress'},
            }.items()},
            'pending_sheets': sum(row['pending_sheets'] or 0 for row in items),
        })
    return groups


def combine_booking_rows(bookings, pending, today):
    groups = defaultdict(list)
    for booking in bookings:
        mapping = booking.therapist_slot_mapping
        if booking.crt_program_booking_id:
            key = ('structured', booking.crt_program_booking_id, booking.patient_id)
        elif booking.group_program_booking_id:
            key = ('group', booking.group_program_booking_id, booking.patient_id)
        else:
            key = ('booking', booking.id, booking.patient_id)
        groups[key].append(booking)
    result = []
    for key, items in groups.items():
        row = items[0]
        mapping = row.therapist_slot_mapping
        program = row.program or (row.crt_program_booking.program if row.crt_program_booking else None) or (
            row.group_program_booking.program if row.group_program_booking else None)
        if not program and row.patient_package and row.patient_package.package:
            program = row.patient_package.package.program
        name = program.program_name if program else 'General'
        codes = [item.patient_slot_booking_status_master.code if item.patient_slot_booking_status_master else item.status for item in items]
        therapists = {item.therapist_slot_mapping.therapist_id: item.therapist_slot_mapping.therapist.name for item in items}
        patient = row.patient
        result.append({
            'key': ':'.join(map(str, key)), 'date': mapping.slot_date.isoformat(),
            'category': category_for(name), 'program': name, 'activity': 'Session',
            'patient_id': row.patient_id, 'child': f'{patient.first_name} {patient.last_name}'.strip() if patient else 'Unassigned',
            'therapist_ids': sorted(therapists), 'therapists': ', '.join(sorted(therapists.values())),
            'therapist_options': [{'id': key, 'name': value} for key, value in sorted(therapists.items())],
            'region_id': patient.region_id if patient else mapping.therapist.region_id,
            'status': booking_status(codes, mapping.slot_date, today),
            'recorded_status': ', '.join(sorted(set(codes))),
            'pending_sheets': sum(pending.get(item.id, 0) for item in items) if pending is not None else None,
        })
    return result
