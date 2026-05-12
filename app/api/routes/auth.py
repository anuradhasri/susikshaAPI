from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.database import get_db
from app.schemas.schemas import (
    ForgotPasswordRequest,
    RefreshTokenRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services.user_service import AuthService, PasswordResetService, UserService
from app.utils.logger import setup_logging

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
logger = setup_logging(__name__)
settings = get_settings()


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
    payload: RefreshTokenRequest,
    db: Session = Depends(get_db)
):
    """Refresh access token using refresh token"""
    tokens = AuthService.rotate_refresh_token(db, payload.refresh_token)
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    return tokens


def _client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _password_policy_error(password: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not any(char.isupper() for char in password):
        return "Password must include at least one uppercase letter"
    if not any(char.isdigit() for char in password):
        return "Password must include at least one number"
    if not any(not char.isalnum() for char in password):
        return "Password must include at least one special character"
    return None


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a password reset token.

    The response is intentionally neutral so unknown emails cannot be enumerated.
    In local/dev environments without SMTP, reset_url is returned to keep testing easy.
    """
    user, token = PasswordResetService.create_reset_token(
        db,
        email=payload.email,
        expires_in_minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    reset_url = None
    if user and token:
        reset_url = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?{urlencode({'token': token})}"

    return {
        "message": "If that email exists, password reset instructions have been prepared.",
        "reset_url": reset_url if reset_url and not settings.email_host else None,
        "email_sent": bool(reset_url and settings.email_host),
    }


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Reset password using a valid reset token."""
    policy_error = _password_policy_error(payload.new_password)
    if policy_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=policy_error)

    if not PasswordResetService.reset_password(db, token=payload.token, new_password=payload.new_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    return {"message": "Password reset successfully"}
