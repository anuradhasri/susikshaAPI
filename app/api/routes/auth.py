from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.schemas import UserCreate, UserLogin, TokenResponse, UserResponse
from app.services.user_service import UserService, AuthService
from app.utils.logger import setup_logging

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
logger = setup_logging(__name__)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_create: UserCreate, db: Session = Depends(get_db)):
    """Register a new user"""
    # Check if user already exists
    existing_user = UserService.get_user_by_email(db, user_create.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    existing_username = UserService.get_user_by_username(db, user_create.username)
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken"
        )
    
    try:
        user = UserService.create_user(db, user_create)
        logger.info(f"User registered: {user.username}", extra={"user_id": user.id})
        return user
    except Exception as e:
        logger.error(f"Error registering user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/login", response_model=TokenResponse)
async def login(user_login: UserLogin, db: Session = Depends(get_db)):
    """Login user and return tokens"""
    user = UserService.authenticate_user(db, user_login.email, user_login.password)
    
    if not user:
        logger.warning(f"Failed login attempt for email: {user_login.email}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    tokens = AuthService.create_tokens(user, db)
    AuthService.update_last_login(db, user.id)
    
    logger.info(f"User logged in: {user.username}", extra={"user_id": user.id})
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    refresh_token: str,
    db: Session = Depends(get_db)
):
    """Refresh access token using refresh token"""
    from app.core.security import decode_token, create_access_token
    
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    user_id = payload.get("user_id")
    user = UserService.get_user_by_id(db, user_id)
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive"
        )
    
    tokens = AuthService.create_tokens(user, db)
    return tokens
