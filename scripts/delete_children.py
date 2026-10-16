"""Delete child-owned database records for one or more patient IDs.

Preview (always rolls back):
    python scripts/delete_children.py 106

Permanent deletion (requires typing DELETE):
    python scripts/delete_children.py 106 --commit
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.constants import CLIENT


BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"
SQL_FILE = Path(__file__).with_name("delete_child_records_by_ids.sql")


def database_url() -> str:
    if not ENV_FILE.exists():
        raise RuntimeError(f"Environment file not found: {ENV_FILE}")
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL was not found in autism-backend/.env")


def connect():
    parsed = urlparse(database_url().replace("mysql+pymysql://", "mysql://", 1))
    return pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=parsed.path.lstrip("/"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        client_flag=CLIENT.MULTI_STATEMENTS,
        autocommit=True,
    )


def build_sql(patient_ids: list[int], commit: bool) -> str:
    sql = SQL_FILE.read_text(encoding="utf-8")
    values = ",\n    ".join(f"({patient_id})" for patient_id in patient_ids)
    sql, replacements = re.subn(
        r"INSERT INTO delete_patient_ids \(patient_id\) VALUES\s*.*?;",
        f"INSERT INTO delete_patient_ids (patient_id) VALUES\n    {values};",
        sql,
        count=1,
        flags=re.DOTALL,
    )
    if replacements != 1:
        raise RuntimeError("Could not locate the patient ID section in the SQL file")
    if commit:
        sql, replacements = re.subn(r"(?m)^ROLLBACK;$", "COMMIT;", sql, count=1)
        if replacements != 1:
            raise RuntimeError("Could not locate the final ROLLBACK in the SQL file")
    return sql


def selected_children(connection, patient_ids: list[int]) -> list[dict]:
    placeholders = ", ".join(["%s"] * len(patient_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, first_name, last_name FROM patients WHERE id IN ({placeholders}) ORDER BY id",
            patient_ids,
        )
        return list(cursor.fetchall())


def execute_script(connection, sql: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        while True:
            if cursor.description:
                rows = cursor.fetchall()
                for row in rows:
                    print(row)
            if not cursor.nextset():
                break


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete all database records belonging to selected child IDs.",
    )
    parser.add_argument("patient_ids", nargs="+", type=int, help="Numeric patients.id values")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Permanently commit deletion. Without this option everything is rolled back.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    patient_ids = sorted(set(args.patient_ids))
    if any(patient_id <= 0 for patient_id in patient_ids):
        print("Patient IDs must be positive integers.", file=sys.stderr)
        return 2

    connection = connect()
    try:
        children = selected_children(connection, patient_ids)
        found_ids = {int(child["id"]) for child in children}
        missing_ids = [patient_id for patient_id in patient_ids if patient_id not in found_ids]

        if not children:
            print("No matching children found. Nothing was changed.")
            return 1

        print("Selected children:")
        for child in children:
            name = " ".join(part for part in [child.get("first_name"), child.get("last_name")] if part)
            print(f"  {child['id']}: {name}")
        if missing_ids:
            print(f"Not found: {', '.join(map(str, missing_ids))}")
            print("Nothing was changed because every requested ID must exist.")
            return 1

        if args.commit:
            confirmation = input("Type DELETE to permanently remove these children and their history: ")
            if confirmation != "DELETE":
                print("Cancelled. Nothing was changed.")
                return 1
        else:
            print("Preview mode: all deletions will be rolled back.")

        execute_script(connection, build_sql(patient_ids, args.commit))
        if args.commit:
            print("Deletion committed successfully.")
        else:
            print("Preview completed and rolled back. Add --commit for permanent deletion.")
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
