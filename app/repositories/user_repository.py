from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import PasswordResetToken, Patient, Role, User, UserRegionMapping, UserRole
from app.schemas.schemas import UserCreate, UserUpdate


class UserRepository:
    """Database access for users, auth support, roles, and regions."""

    @staticmethod
    def create(db: Session, user_create: UserCreate, hashed_password: str) -> User:
        user = User(
            username=user_create.username,
            email=user_create.email,
            hashed_password=hashed_password,
            first_name=user_create.first_name,
            last_name=user_create.last_name,
            region_id=user_create.region_id,
            phone=user_create.phone,
        )
        db.add(user)
        db.flush()
        return user

    @staticmethod
    def get_by_id(db: Session, user_id: int) -> Optional[User]:
        return db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()

    @staticmethod
    def get_by_email(db: Session, email: str) -> Optional[User]:
        return db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()

    @staticmethod
    def get_by_username(db: Session, username: str) -> Optional[User]:
        return db.query(User).filter(User.username == username, User.deleted_at.is_(None)).first()

    @staticmethod
    def update(db: Session, user: User, user_update: UserUpdate) -> User:
        for field, value in user_update.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        db.flush()
        return user

    @staticmethod
    def role_names(db: Session, user_id: int) -> list[str]:
        rows = (
            db.query(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .filter(UserRole.user_id == user_id, UserRole.deleted_at.is_(None), Role.deleted_at.is_(None))
            .all()
        )
        return [name for (name,) in rows]

    @staticmethod
    def region_ids(db: Session, user_id: int) -> list[int]:
        rows = db.query(UserRegionMapping.regionid).filter(UserRegionMapping.userid == user_id).all()
        return [region_id for (region_id,) in rows]

    @staticmethod
    def assign_role(db: Session, user_id: int, role_id: int) -> UserRole:
        user_role = UserRole(user_id=user_id, role_id=role_id)
        db.add(user_role)
        db.flush()
        return user_role

    @staticmethod
    def create_password_reset_token(
        db: Session,
        *,
        user_id: int,
        token_hash: str,
        expires_at: datetime,
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> PasswordResetToken:
        token = PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(token)
        db.flush()
        return token

    @staticmethod
    def get_active_password_reset_token(db: Session, token_hash: str, now: datetime) -> Optional[PasswordResetToken]:
        return (
            db.query(PasswordResetToken)
            .filter(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.is_active.is_(True),
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
            .first()
        )

    @staticmethod
    def get_available_patients(
        db: Session,
        current_user
    ):

        return (
            db.query(Patient)
            .filter(
                Patient.is_available == True,
                Patient.region_id.in_(current_user.region_ids)
            )
            .order_by(Patient.id.desc())
            .all()
        )