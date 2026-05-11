# Routes module
from app.api.routes.auth import router as auth_router
from app.api.routes.appointments import router as appointments_router
from app.api.routes.patients import router as patients_router
from app.api.routes.therapists import router as therapists_router
from app.api.routes.sessions import router as sessions_router
from app.api.routes.billing import router as billing_router
from app.api.routes.ui import router as ui_router

routers = [
    auth_router,
    appointments_router,
    patients_router,
    therapists_router,
    sessions_router,
    billing_router,
    ui_router,
]
