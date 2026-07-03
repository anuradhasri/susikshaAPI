from pathlib import Path
from urllib.parse import unquote, urlparse
import re

import pymysql


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
TEST_PASSWORD_HASH = "$2b$12$cZ144s5j10i11ciZlidnNuj5SxYQ1phj/pbH1vfz6bwHXp33uEJCK"

RBAC_RESOURCES = [
    ("menu.appointments", "menu", "Appointments", None, 10),
    ("menu.enquiries", "menu", "Enquiries", None, 20),
    ("menu.children", "menu", "Children", None, 30),
    ("menu.therapists", "menu", "Therapists", None, 40),
    ("menu.reports", "menu", "Reports", None, 50),
    ("region.switch", "control", "Region switcher", None, 60),
    ("appointment.waitlist", "panel", "Appointment waitlist", None, 70),
    ("appointment.action.create", "action", "Create appointment", None, 80),
    ("appointment.action.edit", "action", "Edit appointment", None, 90),
    ("appointment.action.complete", "action", "Complete appointment", None, 100),
    ("appointment.action.payment", "action", "Appointment payment", None, 110),
    ("appointment.action.cancel_paid", "action", "Paid cancel", None, 120),
    ("appointment.action.cancel_unpaid", "action", "Unpaid cancel", None, 130),
    ("appointment.filter.therapists", "control", "Appointment therapist filter", None, 135),
    ("child.tab.details", "tab", "Child details", None, 140),
    ("child.tab.assessment", "tab", "Assessment", None, 150),
    ("child.tab.therapy", "tab", "Therapy", None, 160),
    ("child.tab.payments", "tab", "Payments", None, 170),
    ("child.action.create", "action", "Create child", None, 180),
    ("child.action.edit_profile", "action", "Edit child profile", None, 190),
    ("child.action.upload_document", "action", "Upload child document", None, 200),
    ("child.action.buy_package", "action", "Buy package", None, 210),
    ("child.action.session_plan", "action", "Session plan", None, 220),
    ("child.action.transaction", "action", "Child transaction", None, 230),
    ("assessment.action.export", "action", "Export assessment", None, 240),
    ("assessment.action.complete", "action", "Complete assessment", None, 250),
]

FRONT_OFFICE_PERMISSION_CODES = {code for code, *_ in RBAC_RESOURCES}
CREATE_PERMISSION_CODES = {
    "appointment.action.create",
    "child.action.create",
    "child.action.upload_document",
    "child.action.buy_package",
    "child.action.transaction",
}
EDIT_PERMISSION_CODES = {
    "appointment.action.edit",
    "appointment.action.complete",
    "appointment.action.payment",
    "child.action.edit_profile",
    "child.action.session_plan",
    "assessment.action.complete",
    "region.switch",
}
DELETE_PERMISSION_CODES = {
    "appointment.action.cancel_paid",
    "appointment.action.cancel_unpaid",
}
THERAPIST_PERMISSION_CODES = {
    "menu.appointments",
    "child.tab.details",
    "child.tab.assessment",
    "child.tab.therapy",
    "assessment.action.export",
}
CENTRAL_HEAD_PERMISSION_CODES = {
    "menu.appointments",
    "appointment.filter.therapists",
}


def _database_url() -> str:
    match = re.search(r"^DATABASE_URL=(.+)$", ENV_FILE.read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError("DATABASE_URL not found in autism-backend/.env")
    return match.group(1).strip()


def _connection():
    parsed = urlparse(_database_url().replace("mysql+pymysql://", "mysql://"))
    return pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=parsed.path.lstrip("/"),
        autocommit=False,
    )


def _exists(cursor, query: str, params: tuple = ()) -> bool:
    cursor.execute(query, params)
    return cursor.fetchone() is not None


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    return _exists(
        cursor,
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        LIMIT 1
        """,
        (table_name, column_name),
    )


def _active_clause(cursor, table_name: str, alias: str | None = None) -> str:
    prefix = f"{alias}." if alias else ""
    clauses = []
    if _column_exists(cursor, table_name, "deleted_at"):
        clauses.append(f"{prefix}deleted_at IS NULL")
    return " AND ".join(clauses)


def _ensure_role(cursor, name: str, description: str):
    role_active = _active_clause(cursor, "roles")
    role_filter = f"name = %s AND {role_active}" if role_active else "name = %s"
    cursor.execute(
        f"""
        INSERT INTO roles (name, description)
        SELECT %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM roles WHERE {role_filter}
        )
        """,
        (name, description, name),
    )


def _ensure_schema(cursor):
    _ensure_role(cursor, "therapist", "Therapist read-only access to own appointments")
    _ensure_role(cursor, "central_head", "Central head read-only access across assigned centres")

    if not _exists(
        cursor,
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'therapists'
          AND column_name = 'user_id'
        LIMIT 1
        """,
    ):
        cursor.execute("ALTER TABLE therapists ADD COLUMN user_id INT NULL")

    if not _exists(
        cursor,
        """
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'therapists'
          AND index_name = 'idx_therapist_user_id'
        LIMIT 1
        """,
    ):
        cursor.execute("CREATE INDEX idx_therapist_user_id ON therapists (user_id)")


def _ensure_rbac_tables(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rbac_resources (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            code VARCHAR(120) NOT NULL UNIQUE,
            resource_type VARCHAR(50) NOT NULL,
            label VARCHAR(150) NOT NULL,
            parent_code VARCHAR(120) NULL,
            display_order INT NOT NULL DEFAULT 0,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_rbac_resources_type (resource_type),
            KEY idx_rbac_resources_active (is_active)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rbac_role_permissions (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            role_id INT NOT NULL,
            resource_id INT NOT NULL,
            can_view TINYINT(1) NOT NULL DEFAULT 0,
            can_create TINYINT(1) NOT NULL DEFAULT 0,
            can_edit TINYINT(1) NOT NULL DEFAULT 0,
            can_delete TINYINT(1) NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_rbac_role_resource (role_id, resource_id),
            KEY idx_rbac_role_permissions_role (role_id),
            KEY idx_rbac_role_permissions_resource (resource_id)
        )
        """
    )
    for code, resource_type, label, parent_code, display_order in RBAC_RESOURCES:
        cursor.execute(
            """
            INSERT INTO rbac_resources (code, resource_type, label, parent_code, display_order, is_active)
            VALUES (%s, %s, %s, %s, %s, 1)
            ON DUPLICATE KEY UPDATE
                resource_type = VALUES(resource_type),
                label = VALUES(label),
                parent_code = VALUES(parent_code),
                display_order = VALUES(display_order),
                is_active = 1
            """,
            (code, resource_type, label, parent_code, display_order),
        )

    role_permissions = {
        "admin": FRONT_OFFICE_PERMISSION_CODES,
        "front_office": FRONT_OFFICE_PERMISSION_CODES,
        "frontoffice": FRONT_OFFICE_PERMISSION_CODES,
        "front_officer": FRONT_OFFICE_PERMISSION_CODES,
        "therapist": THERAPIST_PERMISSION_CODES,
        "central_head": CENTRAL_HEAD_PERMISSION_CODES,
    }
    for role_name, codes in role_permissions.items():
        _ensure_role(cursor, role_name, f"{role_name.replace('_', ' ').title()} role")
        cursor.execute("SELECT id FROM roles WHERE name = %s LIMIT 1", (role_name,))
        role_id = int(cursor.fetchone()[0])
        for code, *_ in RBAC_RESOURCES:
            enabled = code in codes
            cursor.execute(
                """
                INSERT INTO rbac_role_permissions (
                    role_id, resource_id, can_view, can_create, can_edit, can_delete
                )
                SELECT %s, resource.id, %s, %s, %s, %s
                FROM rbac_resources resource
                WHERE resource.code = %s
                ON DUPLICATE KEY UPDATE
                    can_view = VALUES(can_view),
                    can_create = VALUES(can_create),
                    can_edit = VALUES(can_edit),
                    can_delete = VALUES(can_delete)
                """,
                (
                    role_id,
                    1 if enabled else 0,
                    1 if enabled and code in CREATE_PERMISSION_CODES else 0,
                    1 if enabled and code in EDIT_PERMISSION_CODES else 0,
                    1 if enabled and code in DELETE_PERMISSION_CODES else 0,
                    code,
                ),
            )


def _upsert_user(cursor, *, email: str, first_name: str, last_name: str, phone: str) -> int:
    user_active = _active_clause(cursor, "users")
    user_filter = f"email = %s AND {user_active}" if user_active else "email = %s"
    cursor.execute(f"SELECT id FROM users WHERE {user_filter} LIMIT 1", (email,))
    row = cursor.fetchone()
    if row:
        user_id = int(row[0])
        cursor.execute(
            """
            UPDATE users
            SET username = %s,
                hashed_password = %s,
                first_name = %s,
                last_name = %s,
                phone = %s,
                is_active = 1,
                is_verified = 1
            WHERE id = %s
            """,
            (email, TEST_PASSWORD_HASH, first_name, last_name, phone, user_id),
        )
        return user_id

    cursor.execute(
        """
        INSERT INTO users (
            username, email, hashed_password, first_name, last_name,
            phone, is_active, is_verified
        )
        VALUES (%s, %s, %s, %s, %s, %s, 1, 1)
        """,
        (email, email, TEST_PASSWORD_HASH, first_name, last_name, phone),
    )
    return int(cursor.lastrowid)


def _assign_role(cursor, user_id: int, role_name: str):
    role_active = _active_clause(cursor, "roles")
    role_filter = f"name = %s AND {role_active}" if role_active else "name = %s"
    cursor.execute(f"SELECT id FROM roles WHERE {role_filter} LIMIT 1", (role_name,))
    role = cursor.fetchone()
    if not role:
        raise RuntimeError(f"Role not found: {role_name}")
    role_id = int(role[0])
    user_role_active = _active_clause(cursor, "user_roles")
    user_role_filter = f"AND {user_role_active}" if user_role_active else ""
    cursor.execute(
        f"""
        INSERT INTO user_roles (user_id, role_id)
        SELECT %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM user_roles
            WHERE user_id = %s
              AND role_id = %s
              {user_role_filter}
        )
        """,
        (user_id, role_id, user_id, role_id),
    )


def _map_region(cursor, user_id: int, region_id: int):
    cursor.execute(
        """
        INSERT INTO user_region_mapping (userid, regionid, created_by, updated_by)
        SELECT %s, %s, %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM user_region_mapping
            WHERE userid = %s
              AND regionid = %s
        )
        """,
        (user_id, region_id, user_id, user_id, user_id, region_id),
    )


def _replace_region_mappings(cursor, user_id: int, region_ids: list[int]):
    if not region_ids:
        return
    placeholders = ", ".join(["%s"] * len(region_ids))
    cursor.execute(
        f"DELETE FROM user_region_mapping WHERE userid = %s AND regionid NOT IN ({placeholders})",
        (user_id, *region_ids),
    )
    for region_id in region_ids:
        _map_region(cursor, user_id, region_id)


def _preferred_region_id(cursor) -> int:
    cursor.execute(
        """
        SELECT id
        FROM regions
        WHERE LOWER(name) LIKE '%mulund%'
           OR LOWER(code) LIKE '%mulund%'
           OR LOWER(location) LIKE '%mulund%'
        ORDER BY id
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    if row:
        return int(row[0])

    cursor.execute("SELECT id FROM regions ORDER BY id LIMIT 1")
    row = cursor.fetchone()
    return int(row[0]) if row else 1


def _split_name(full_name: str) -> tuple[str, str]:
    parts = [part for part in (full_name or "").split() if part]
    if len(parts) <= 1:
        return (parts[0] if parts else "Test"), "Therapist"
    return parts[0], " ".join(parts[1:])


def main():
    with _connection() as connection:
        with connection.cursor() as cursor:
            _ensure_schema(cursor)
            _ensure_rbac_tables(cursor)
            fixed_region_id = _preferred_region_id(cursor)

            therapist_active = _active_clause(cursor, "therapists")
            therapist_where = "WHERE is_active = 1"
            if therapist_active:
                therapist_where += f" AND {therapist_active}"
            cursor.execute(
                f"""
                SELECT id, name, COALESCE(region_id, 1)
                FROM therapists
                {therapist_where}
                ORDER BY id
                LIMIT 1
                """
            )
            therapist = cursor.fetchone()
            if not therapist:
                raise RuntimeError("No active therapist found to link therapist.test user")

            therapist_id = int(therapist[0])
            therapist_first, therapist_last = _split_name(therapist[1])
            therapist_region_id = fixed_region_id

            therapist_user_id = _upsert_user(
                cursor,
                email="therapist.test@sushiksha.local",
                first_name=therapist_first,
                last_name=therapist_last,
                phone="9000000001",
            )
            _assign_role(cursor, therapist_user_id, "therapist")
            _replace_region_mappings(cursor, therapist_user_id, [therapist_region_id])
            cursor.execute(
                """
                UPDATE therapists
                SET user_id = %s
                WHERE id = %s
                  AND (user_id IS NULL OR user_id = %s)
                """,
                (therapist_user_id, therapist_id, therapist_user_id),
            )

            central_user_id = _upsert_user(
                cursor,
                email="centralhead.test@sushiksha.local",
                first_name="Central",
                last_name="Head",
                phone="9000000002",
            )
            _assign_role(cursor, central_user_id, "central_head")
            _replace_region_mappings(cursor, central_user_id, [fixed_region_id])

            connection.commit()

            user_active = _active_clause(cursor, "users", "u")
            user_filter = "u.email IN ('therapist.test@sushiksha.local', 'centralhead.test@sushiksha.local')"
            if user_active:
                user_filter += f" AND {user_active}"
            role_active = _active_clause(cursor, "roles", "r")
            role_join_active = f" AND {role_active}" if role_active else ""
            user_role_active = _active_clause(cursor, "user_roles", "ur")
            user_role_join_active = f" AND {user_role_active}" if user_role_active else ""
            cursor.execute(
                f"""
                SELECT u.email, r.name
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id{user_role_join_active}
                JOIN roles r ON r.id = ur.role_id{role_join_active}
                WHERE {user_filter}
                ORDER BY u.email, r.name
                """
            )
            print("users:", cursor.fetchall())

            cursor.execute(
                """
                SELECT t.id, t.name, u.email
                FROM therapists t
                LEFT JOIN users u ON u.id = t.user_id
                WHERE u.email = 'therapist.test@sushiksha.local'
                """
            )
            print("therapist_link:", cursor.fetchall())

            cursor.execute(
                """
                SELECT u.email, r.id, r.name
                FROM users u
                JOIN user_region_mapping urm ON urm.userid = u.id
                JOIN regions r ON r.id = urm.regionid
                WHERE u.email IN ('therapist.test@sushiksha.local', 'centralhead.test@sushiksha.local')
                ORDER BY u.email, r.id
                """
            )
            print("regions:", cursor.fetchall())


if __name__ == "__main__":
    main()
