from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timedelta
from app.models.models import User, UserRole, Role
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token
from app.schemas.schemas import UserCreate, UserUpdate
from app.utils.query_utils import soft_delete, filter_by_region


class UserService:
    """Service for user operations"""
    
    @staticmethod
    def create_user(db: Session, user_create: UserCreate) -> User:
        """Create a new user"""
        hashed_password = hash_password(user_create.password)
        
        db_user = User(
            username=user_create.username,
            email=user_create.email,
            hashed_password=hashed_password,
            first_name=user_create.first_name,
            last_name=user_create.last_name,
            region_id=user_create.region_id,
            phone=user_create.phone
        )
        
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
    
    @staticmethod
    def get_user_by_username(db: Session, username: str) -> User:
        """Get user by username"""
        return db.query(User).filter(
            User.username == username,
            User.deleted_at.is_(None)
        ).first()
    
    @staticmethod
    def get_user_by_email(db: Session, email: str) -> User:
        """Get user by email"""
        return db.query(User).filter(
            User.email == email,
            User.deleted_at.is_(None)
        ).first()
    
    @staticmethod
    def get_user_by_id(db: Session, user_id: int) -> User:
        """Get user by ID"""
        return db.query(User).filter(
            User.id == user_id,
            User.deleted_at.is_(None)
        ).first()
    
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
        roles = db.query(Role).join(UserRole).filter(
            UserRole.user_id == user_id,
            UserRole.deleted_at.is_(None),
            Role.deleted_at.is_(None)
        ).all()
        
        return [role.name for role in roles]
    
    @staticmethod
    def assign_role(db: Session, user_id: int, role_id: int) -> UserRole:
        """Assign role to user"""
        user_role = UserRole(user_id=user_id, role_id=role_id)
        db.add(user_role)
        db.commit()
        db.refresh(user_role)
        return user_role
    
    @staticmethod
    def update_user(db: Session, user_id: int, user_update: UserUpdate) -> User:
        """Update user"""
        user = UserService.get_user_by_id(db, user_id)
        if not user:
            return None
        
        for field, value in user_update.dict(exclude_unset=True).items():
            setattr(user, field, value)
        
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
        roles = UserService.get_user_roles(db, user.id)
        
        token_data = {
            "user_id": user.id,
            "username": user.username,
            "region_id": user.region_id,
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
    def update_last_login(db: Session, user_id: int):
        """Update last login timestamp"""
        user = UserService.get_user_by_id(db, user_id)
        if user:
            user.last_login = datetime.utcnow()
            db.commit()
