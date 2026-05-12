import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import User, UserRole, Role, UserRegionMapping
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token
from app.schemas.schemas import UserCreate, UserUpdate
from app.repositories.user_repository import UserRepository
from app.utils.query_utils import soft_delete, filter_by_region


class UserService:
    """Service for user operations"""
    
    @staticmethod
    def create_user(db: Session, user_create: UserCreate) -> User:
        """Create a new user"""
        hashed_password = hash_password(user_create.password)
        db_user = UserRepository.create(db, user_create, hashed_password)
        db.commit()
        db.refresh(db_user)
        return db_user
    
    @staticmethod
    def get_user_by_username(db: Session, username: str) -> User:
        """Get user by username"""
        return UserRepository.get_by_username(db, username)
    
    @staticmethod
    def get_user_by_email(db: Session, email: str) -> User:
        """Get user by email"""
        return UserRepository.get_by_email(db, email)
    
    @staticmethod
    def get_user_by_id(db: Session, user_id: int) -> User:
        """Get user by ID"""
        return UserRepository.get_by_id(db, user_id)
    
    @staticmethod
    def authenticate_user(db: Session, email: str, password: str) -> User:
        """Authenticate user by email and return user object"""
        user = UserService.get_user_by_email(db, email)
        if not user:
            return None
        
        if not verify_password(password, user.hashed_password):
            return None
        
        return user
    
    @staticmethod
    def get_user_roles(db: Session, user_id: int) -> list:
        """Get roles for a user"""
        return UserRepository.role_names(db, user_id)
    
    @staticmethod
    def assign_role(db: Session, user_id: int, role_id: int) -> UserRole:
        """Assign role to user"""
        user_role = UserRepository.assign_role(db, user_id, role_id)
        db.commit()
        db.refresh(user_role)
        return user_role
    
    @staticmethod
    def update_user(db: Session, user_id: int, user_update: UserUpdate) -> User:
        """Update user"""
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            return None
        
        UserRepository.update(db, user, user_update)
        db.commit()
        db.refresh(user)
        return user
    
    @staticmethod
    def delete_user(db: Session, user_id: int) -> bool:
        """Soft delete user"""
        user = soft_delete(db, User, user_id)
        return user is not None
    
    @staticmethod
    def list_users(db: Session, region_id: int = None, skip: int = 0, limit: int = 100) -> tuple:
        """List users with optional region filtering"""
        query = db.query(User).filter(User.deleted_at.is_(None))
        
        if region_id:
            query = filter_by_region(query, region_id, User)
        
        total = query.count()
        users = query.offset(skip).limit(limit).all()
        
        return users, total


class AuthService:
    """Service for authentication operations"""
    
    @staticmethod
    def create_tokens(user: User, db: Session) -> dict:
        """Create access and refresh tokens for user"""
        roles = UserRepository.role_names(db, user.id)
        region_ids = UserRepository.region_ids(db, user.id)
        
        token_data = {
            "user_id": user.id,
            "username": user.username,
            "region_id": region_ids[0] if region_ids else None,
            "region_ids": region_ids,
            "roles": roles,
            "email": user.email
        }
        
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }

    @staticmethod
    def rotate_refresh_token(db: Session, refresh_token: str) -> Optional[dict]:
        """Validate a refresh token and issue a fresh token pair."""
        from app.core.security import decode_token

        payload = decode_token(refresh_token)
        if not payload or payload.get("type") != "refresh":
            return None

        user = UserRepository.get_by_id(db, payload.get("user_id"))
        if not user or not user.is_active:
            return None

        return AuthService.create_tokens(user, db)
    
    @staticmethod
    def update_last_login(db: Session, user_id: int):
        """Update last login timestamp"""
        user = UserService.get_user_by_id(db, user_id)
        if user:
            user.last_login = datetime.utcnow()
            db.commit()


class PasswordResetService:
    """Password reset token lifecycle."""

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def create_reset_token(
        db: Session,
        *,
        email: str,
        expires_in_minutes: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[Optional[User], Optional[str]]:
        user = UserRepository.get_by_email(db, email)
        if not user or not user.is_active:
            return None, None

        raw_token = secrets.token_urlsafe(32)
        UserRepository.create_password_reset_token(
            db,
            user_id=user.id,
            token_hash=PasswordResetService._hash_token(raw_token),
            expires_at=datetime.utcnow() + timedelta(minutes=expires_in_minutes),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.commit()
        return user, raw_token

    @staticmethod
    def reset_password(db: Session, *, token: str, new_password: str) -> bool:
        reset_token = UserRepository.get_active_password_reset_token(
            db,
            PasswordResetService._hash_token(token),
            datetime.utcnow(),
        )
        if not reset_token:
            return False

        user = UserRepository.get_by_id(db, reset_token.user_id)
        if not user or not user.is_active:
            return False

        user.hashed_password = hash_password(new_password)
        reset_token.is_active = False
        reset_token.used_at = datetime.utcnow()
        db.commit()
        return True
