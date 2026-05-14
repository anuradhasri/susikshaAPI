import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Autism Therapy Management System"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://root:root@localhost:3306/autism_therapy"
    )

    # Redis
    REDIS_URL: str = os.getenv(
        "REDIS_URL",
        "redis://localhost:6379/0"
    )
    REDIS_CACHE_TTL: int = 300  # 5 minutes

    # JWT
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY",
        "your-secret-key-change-in-production-change-this-to-a-secure-random-string"
    )

    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    FRONTEND_URL: str = os.getenv(
        "FRONTEND_URL",
        "http://localhost:5173"
    )

    # Email
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = "Sushiksha <no-reply@sushiksha.com>"
    MAIL_PORT: int = 465
    MAIL_SERVER: str = ""
    MAIL_USE_SSL: bool = True
    MAIL_USE_TLS: bool = False

    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "")
    SMTP_USE_SSL: bool = os.getenv(
        "SMTP_USE_SSL",
        ""
    ).lower() == "true"

    SMTP_USE_TLS: bool = os.getenv(
        "SMTP_USE_TLS",
        "true"
    ).lower() == "true"

    # Uploads
    DOCUMENT_UPLOAD_PATH: str = os.getenv(
        "DOCUMENT_UPLOAD_PATH",
        "C:/uploads/documents"
    )
    MAX_DOCUMENT_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_DOCUMENT_UPLOAD_SIZE_MB", "20"))
    DOCUMENT_ALLOWED_EXTENSIONS: str = os.getenv(
        "DOCUMENT_ALLOWED_EXTENSIONS",
        ".pdf,.doc,.docx,.jpg,.jpeg,.png"
    )

    @property
    def email_host(self) -> str:
        return self.SMTP_HOST or self.MAIL_SERVER

    @property
    def email_port(self) -> int:
        return self.SMTP_PORT if self.SMTP_HOST else self.MAIL_PORT

    @property
    def email_username(self) -> str:
        return self.SMTP_USERNAME or self.MAIL_USERNAME

    @property
    def email_password(self) -> str:
        return self.SMTP_PASSWORD or self.MAIL_PASSWORD

    @property
    def email_from(self) -> str:
        return self.SMTP_FROM_EMAIL or self.MAIL_FROM

    @property
    def email_use_ssl(self) -> bool:
        if self.SMTP_HOST:
            return self.SMTP_USE_SSL or self.SMTP_PORT == 465

        return self.MAIL_USE_SSL or self.MAIL_PORT == 465

    @property
    def email_use_tls(self) -> bool:
        if self.email_use_ssl:
            return False

        return (
            self.SMTP_USE_TLS
            if self.SMTP_HOST
            else self.MAIL_USE_TLS
        )

    # CORS
    CORS_ORIGINS: list = [
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]

    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list = ["*"]
    CORS_ALLOW_HEADERS: list = ["*"]

    # Logging
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings():
    return Settings()
