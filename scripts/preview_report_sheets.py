"""Isolated UI verification server. All sample sheets live in memory, never in the configured database."""
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_report_sheets import ReportSheetTests
from app.services.report_sheet_catalog import provision_report_sheets
from app.core.database import get_db
from app.api.routes.report_sheets import router

fixture = ReportSheetTests()
fixture.setUp()
provision_report_sheets(fixture.db)
for day in ['2026-09-18', '2026-08-03', '2026-09-17', '2026-09-10', '2026-09-09', '2026-09-08', '2026-09-07', '2026-09-06', '2026-09-05', '2026-09-04', '2026-09-03', '2026-09-02']:
    fixture.observation(day)
fixture.goal()
app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_db] = lambda: fixture.db
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5174', 'http://localhost:5174'], allow_methods=['*'], allow_headers=['*'])
permissions = {key: {'view': True, 'create': True} for key in ['menu.reports', 'menu.children', 'menu.therapists', 'report.action.create_sheet', 'region.switch']}
profile = {'id': 1, 'user_id': 1, 'email': 'test@example.test', 'username': 'test@example.test', 'full_name': 'Preview User', 'region_id': 1, 'region_ids': [1], 'roles': [{'role_id': 1, 'role_name': 'admin'}], 'user_roles': [{'roles': {'id': 1, 'name': 'admin'}}], 'permissions': permissions}

@app.post('/api/v1/ui/auth/login')
def login():
    return {'data': {'session': {'access_token': 'isolated-preview-token', 'token_type': 'bearer'}, 'user': profile}}

@app.get('/api/v1/ui/auth/me')
def me():
    return {'data': {'user': profile}}

@app.get('/api/v1/masters/regions')
def regions():
    return [{'id': 1, 'name': 'Centre One', 'code': 'ONE', 'category': 'region'}]

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8013)
