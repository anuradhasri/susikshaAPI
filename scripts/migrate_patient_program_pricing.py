from sqlalchemy import text

from app.core.database import engine


MYSQL_SQL = """
CREATE TABLE IF NOT EXISTS patient_program_pricing (
    id INT AUTO_INCREMENT PRIMARY KEY,
    patient_id INT NOT NULL,
    program_id INT NOT NULL,
    billing_type VARCHAR(24) NOT NULL,
    agreed_amount FLOAT NOT NULL,
    total_sessions INT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by INT NULL,
    updated_by INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at DATETIME NULL,
    CONSTRAINT fk_patient_program_pricing_patient FOREIGN KEY (patient_id) REFERENCES patients(id),
    CONSTRAINT fk_patient_program_pricing_program FOREIGN KEY (program_id) REFERENCES programs(id),
    CONSTRAINT fk_patient_program_pricing_created_by FOREIGN KEY (created_by) REFERENCES users(id),
    CONSTRAINT fk_patient_program_pricing_updated_by FOREIGN KEY (updated_by) REFERENCES users(id),
    CONSTRAINT uq_patient_program_pricing_effective UNIQUE (patient_id, program_id, effective_from),
    INDEX idx_patient_program_pricing_patient (patient_id),
    INDEX idx_patient_program_pricing_program (program_id),
    INDEX idx_patient_program_pricing_lookup (patient_id, program_id, effective_from, effective_to)
)
"""


SQLITE_SQL = """
CREATE TABLE IF NOT EXISTS patient_program_pricing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(id),
    program_id INTEGER NOT NULL REFERENCES programs(id),
    billing_type VARCHAR(24) NOT NULL,
    agreed_amount FLOAT NOT NULL,
    total_sessions INTEGER NULL,
    effective_from DATE NOT NULL,
    effective_to DATE NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_by INTEGER NULL REFERENCES users(id),
    updated_by INTEGER NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME NULL,
    UNIQUE (patient_id, program_id, effective_from)
)
"""


def main():
    with engine.begin() as connection:
        connection.execute(text(SQLITE_SQL if connection.dialect.name == "sqlite" else MYSQL_SQL))
        if connection.dialect.name == "sqlite":
            connection.execute(text("CREATE INDEX IF NOT EXISTS idx_patient_program_pricing_lookup ON patient_program_pricing (patient_id, program_id, effective_from, effective_to)"))
    print("patient_program_pricing is ready")


if __name__ == "__main__":
    main()
