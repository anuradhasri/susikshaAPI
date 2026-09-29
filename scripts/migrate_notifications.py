"""Create the in-app notifications table when upgrading an existing database."""
from app.core.database import engine
from app.models.models import Notification


def main() -> None:
    Notification.__table__.create(bind=engine, checkfirst=True)
    print("notifications table ready")


if __name__ == "__main__":
    main()
