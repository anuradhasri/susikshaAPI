"""Add persistent read state for actionable therapy-sheet notifications."""
from sqlalchemy import inspect, text
from app.core.database import engine


def main() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("therapy_sheet_assignment")}
    if "notification_read_at" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE therapy_sheet_assignment ADD COLUMN notification_read_at DATETIME NULL AFTER notified_at"))
    print("therapy sheet notification read state ready")


if __name__ == "__main__":
    main()
