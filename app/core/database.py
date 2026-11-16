from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import QueuePool
from app.core.config import get_settings

settings = get_settings()

# Create engine with connection pooling
engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=settings.DEBUG,
)

class AppSession(Session):
    """Application transactions carry committed live-update metadata."""
    pass


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AppSession)
Base = declarative_base()


def get_db(request: Request):
    """Dependency for getting database session"""
    db = SessionLocal()
    db.info["live_origin"] = request.headers.get("x-realtime-client", "")[:128]
    db.info["live_actor_id"] = getattr(request.state, "user_id", None)
    try:
        yield db
    finally:
        db.close()


# Initialize database
def init_db():
    """Create the additive shared-package mapping; leave existing tables unchanged."""
    from app.models.models import PackageProgramAssignment
    PackageProgramAssignment.__table__.create(bind=engine, checkfirst=True)

    # Add configurable TDS without rebuilding existing payout tables.
    from sqlalchemy import inspect, text
    from app.models.payouts import PayrollTaxSetting, TherapistMonthlyPayout
    PayrollTaxSetting.__table__.create(bind=engine, checkfirst=True)
    for table, columns in {
        "payroll_tax_settings": {"tds_threshold": "FLOAT NOT NULL DEFAULT 100000", "tds_rate": "FLOAT NOT NULL DEFAULT 10"},
        "therapist_monthly_payout": {"tds_amount": "FLOAT NOT NULL DEFAULT 0", "actual_salary": "FLOAT NULL", "professional_tax_amount": "FLOAT NULL"},
    }.items():
        inspector = inspect(engine)
        if not inspector.has_table(table):
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        with engine.begin() as connection:
            for column, definition in columns.items():
                if column not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
