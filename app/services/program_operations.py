"""Read-only operational reporting helpers; no billing or payout synchronization."""
from collections import defaultdict
from datetime import datetime, timedelta

CATEGORIES = ['General', 'Structured', 'Parent', 'Schooling', 'Academic Intervention', 'Vocational', 'Assessment', 'Other']


def program_display_name(name):
    name = (name or '').strip() or 'General'
    normalized = name.lower().replace('-', ' ').replace('_', ' ')
    if normalized in ('crt', 'crt program', 'crt programming', 'structured', 'structured program', 'structured programming'):
        return 'Structured Programming'
    return name


def category_for(name):
    name = (name or '').lower()
    if 'academic' in name:
        return 'Academic Intervention'
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
    codes = [str(code or '').strip().upper() for code in codes]
    active_codes = [code for code in codes if 'CANCEL' not in code]
    if codes and not active_codes:
        return 'Cancelled'
    if active_codes and all(code == 'COMPLETED' for code in active_codes):
        return 'Completed'
    if any(code in {'COMPLETED', 'IN_PROGRESS'} for code in active_codes):
        return 'In progress'
    if 'BOOKED' in active_codes:
        return 'Booked'
    return 'Pending' if day < today else 'Booked'



def summarize_operations(rows, programs=None):
    groups = []
    for category in dict.fromkeys([*(programs or []), *(row['category'] for row in rows)]):
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
        name = program_display_name(program.program_name if program else 'General')
        therapy_names = sorted({item.therapist_slot_mapping.therapy.name.strip() for item in items
                                if item.therapist_slot_mapping.therapy and item.therapist_slot_mapping.therapy.name})
        codes = [item.patient_slot_booking_status_master.code if item.patient_slot_booking_status_master else item.status for item in items]
        therapists = {item.therapist_slot_mapping.therapist_id: item.therapist_slot_mapping.therapist.name for item in items}
        sheet_counts = defaultdict(int)
        if pending is not None:
            for item in items:
                for sheet_type, count in pending.get(item.id, {}).items():
                    sheet_counts[sheet_type] += count
        windows = []
        for item in items:
            slot = item.therapist_slot_mapping.slot
            if slot:
                start = datetime.combine(mapping.slot_date, slot.start_time)
                duration = item.duration_minutes or (program.duration_minutes if program and key[0] != 'structured' else None)
                end = start + timedelta(minutes=duration) if duration else datetime.combine(mapping.slot_date, slot.end_time)
                windows.append((start, end))
        patient = row.patient
        result.append({
            'key': ':'.join(map(str, key)), 'date': mapping.slot_date.isoformat(),
            'slot_start': min(start for start, _ in windows).strftime('%H:%M') if windows else None,
            'slot_end': max(end for _, end in windows).strftime('%H:%M') if windows else None,
            'category': name, 'program': name,
            'therapy': ', '.join(therapy_names) or 'Therapy not recorded', 'activity': 'Session',
            'patient_id': row.patient_id, 'child': f'{patient.first_name} {patient.last_name}'.strip() if patient else 'Unassigned',
            'therapist_ids': sorted(therapists), 'therapists': ', '.join(sorted(therapists.values())),
            'therapist_options': [{'id': key, 'name': value} for key, value in sorted(therapists.items())],
            'region_id': patient.region_id if patient else mapping.therapist.region_id,
            'status': booking_status(codes, mapping.slot_date, today),
            'recorded_status': ', '.join(sorted(set(codes))),
            'pending_sheets': sum(sheet_counts.values()) if pending is not None else None,
            'pending_sheet_types': dict(sorted(sheet_counts.items())),
        })
    return result
