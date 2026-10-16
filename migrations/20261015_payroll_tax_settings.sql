CREATE TABLE IF NOT EXISTS payroll_tax_settings (
    id INT NOT NULL AUTO_INCREMENT,
    income_threshold FLOAT NOT NULL DEFAULT 25000,
    regular_month_amount FLOAT NOT NULL DEFAULT 200,
    march_amount FLOAT NOT NULL DEFAULT 300,
    updated_by INT NULL,
    created_at DATETIME NULL,
    updated_at DATETIME NULL,
    deleted_at DATETIME NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_payroll_tax_settings_updated_by FOREIGN KEY (updated_by) REFERENCES users(id)
);

INSERT INTO payroll_tax_settings (id, income_threshold, regular_month_amount, march_amount, created_at, updated_at)
SELECT 1, 25000, 200, 300, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM payroll_tax_settings WHERE id = 1);
