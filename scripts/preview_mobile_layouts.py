"""Fictional, read-only mobile layout fixtures. Never connects to production data."""
from scripts.preview_report_sheets import app, permissions
from datetime import date
for code in ['menu.appointments', 'menu.enquiries', 'appointment.filter.therapists', 'appointment.waitlist', 'appointment.action.create']:
    permissions[code] = {'view': True, 'create': True}
patients = [{'id': 1, 'full_name': 'Preview Child', 'first_name': 'Preview', 'last_name': 'Child', 'date_of_birth': '2020-01-01', 'gender': 'male', 'region_id': 1, 'father_name': 'Preview Parent', 'phone': '0000000000', 'diagnosis': 'Sample diagnosis', 'is_active': True, 'created_at': '2026-09-01T00:00:00'}]
therapists = [{'id': 1, 'name': 'Preview Therapist', 'region_id': 1, 'specialization': 'Speech Therapy', 'is_active': True, 'users': {'full_name': 'Preview Therapist', 'email': 'preview@example.test', 'phone': '0000000000'}}]
@app.get('/api/v1/ui/{table}')
def listing(table: str):
    rows = patients if table == 'patients' else therapists if table == 'therapists' else [{'id': 1, 'first_name': 'Sample', 'last_name': 'Enquiry', 'gender': 'female', 'phone': '0000000000', 'program_name': 'Speech therapy', 'referred_by': 'Preview source', 'enquiry_source': 'Website', 'is_active': True}] if table == 'enquiries' else []
    return {'data': rows, 'count': len(rows)}
@app.get('/api/v1/appointments/calendar')
def calendar(selected_date: str = None, start_date: str = None, end_date: str = None):
    day = selected_date or start_date or date.today().isoformat()
    slots = [{'slot_id': 1, 'start_time': '09:00:00', 'end_time': '09:45:00', 'slot_date': day, 'status': 'booked', 'patient_id': 1, 'patient_name': 'Preview Child', 'patient_slot_booking_id': 1, 'therapy_id': 1, 'therapy_name': 'Speech Therapy', 'program_id': 1, 'program_type': 'individual', 'participants': []}, {'slot_id': 2, 'start_time': '10:00:00', 'end_time': '10:45:00', 'slot_date': day, 'status': 'completed', 'patient_id': 1, 'patient_name': 'Preview Child', 'patient_slot_booking_id': 2, 'therapy_id': 1, 'therapy_name': 'Speech Therapy', 'program_id': 1, 'program_type': 'individual', 'participants': []}]
    return {'therapists': [{'therapist_id': 1, 'therapist_name': 'Preview Therapist', 'therapy_name': 'Speech Therapy', 'slots': slots}]}
@app.get('/api/v1/appointments/{resource}')
def masters(resource: str):
    if resource == 'slots': return [{'id': 1, 'start_time': '09:00:00', 'end_time': '09:45:00'}]
    return {'data': []}
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8013)
