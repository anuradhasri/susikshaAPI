from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from typing import Optional
from app.core.database import get_db
from app.core.security import decode_token, TokenData
from app.models.models import User, UserRole, Role, UserRegionMapping
from app.core.config import get_settings

settings = get_settings()
security = HTTPBearer()


def _user_region_ids(db: Session, user_id: int) -> list[int]:
    return [
        region_id
        for (region_id,) in (
            db.query(UserRegionMapping.regionid)
            .filter(UserRegionMapping.userid == user_id)
            .all()
        )
    ]


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Get current authenticated user from token"""
    token = credentials.credentials
    
    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    
    user_id: int = payload.get("user_id")
    username: str = payload.get("username")
    
    if not user_id or not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    
    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )
    
    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> User:
    """Verify current user is an admin"""
    admin_role = db.query(Role).filter(Role.name == "admin", Role.deleted_at.is_(None)).first()
    
    if not admin_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role not found",
        )
    
    user_role = db.query(UserRole).filter(
        UserRole.user_id == current_user.id,
        UserRole.role_id == admin_role.id,
        UserRole.deleted_at.is_(None)
    ).first()
    
    if not user_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can access this resource",
        )
    
    return current_user


async def get_user_roles(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> list:
    """Get roles for current user"""
    user_roles = db.query(Role).join(UserRole).filter(
        UserRole.user_id == current_user.id,
        UserRole.deleted_at.is_(None),
        Role.deleted_at.is_(None)
    ).all()
    
    return [role.name for role in user_roles]


async def check_region_access(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    target_region_id: Optional[int] = None
) -> bool:
    """Check if user can access target region"""
    roles = await get_user_roles(current_user, db)
    
    # Admin has access to all regions
    if "admin" in roles:
        return True
    
    # Non-admin users can only access their own region
    region_ids = _user_region_ids(db, current_user.id)
    if target_region_id and target_region_id not in region_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this region",
        )
    
    return True


def get_token_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> TokenData:
    """Extract token data for current user"""
    roles = []
    user_roles = db.query(Role).join(UserRole).filter(
        UserRole.user_id == current_user.id,
        UserRole.deleted_at.is_(None)
    ).all()
    
    roles = [role.name for role in user_roles]
    region_ids = _user_region_ids(db, current_user.id)
    
    return TokenData(
        user_id=current_user.id,
        username=current_user.username,
        region_id=region_ids[0] if region_ids else None,
        roles=roles
    )
