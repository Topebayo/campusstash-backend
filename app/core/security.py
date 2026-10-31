import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from passlib.context import CryptContext
from jose import jwt, JWTError, ExpiredSignatureError
from fastapi import HTTPException, status

from app.core.config import settings

# CryptContext configured with bcrypt algorithm
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

COMMON_WEAK_PASSWORDS = {
    "password", "password123", "12345678", "123456789", "1234567890",
    "qwertyuiop", "admin123", "campusstash", "welcome1", "student123"
}

def validate_password_strength(password: str) -> None:
    """
    Production-grade password validation:
    - Minimum length of 8 characters.
    - Maximum length of 72 characters (Bcrypt safe boundary).
    - Checks for common trivial passwords.
    """
    if not password or len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters long."
        )
    if len(password) > 72:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password cannot exceed 72 characters."
        )
    if password.lower() in COMMON_WEAK_PASSWORDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is too common or easily guessable. Please choose a stronger password."
        )

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a raw password against the stored bcrypt hash.
    Bcrypt inherently caps input at 72 bytes.
    """
    if not plain_password or not hashed_password:
        return False
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    """
    Generate a secure bcrypt password hash.
    """
    validate_password_strength(password)
    return pwd_context.hash(password)

def create_access_token(
    subject: str | Any, 
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[Dict[str, Any]] = None
) -> str:
    """
    Create a signed JWT access token.
    Default lifetime: settings.ACCESS_TOKEN_EXPIRE_MINUTES
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode: Dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": expire,
        "type": "access"
    }
    if extra_claims:
        to_encode.update(extra_claims)
        
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.ALGORITHM)
    return encoded_jwt

def create_refresh_token(
    subject: str | Any, 
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create a signed JWT refresh token for session renewals.
    Default lifetime: settings.REFRESH_TOKEN_EXPIRE_DAYS
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        
    to_encode: Dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": expire,
        "type": "refresh"
    }
    
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.ALGORITHM)
    return encoded_jwt

def decode_token(token: str) -> Dict[str, Any]:
    """
    Decode and validate a JWT token's signature, algorithm, and expiration.
    Raises ExpiredSignatureError or JWTError if invalid.
    """
    return jwt.decode(
        token, 
        settings.JWT_SECRET, 
        algorithms=[settings.ALGORITHM]
    )
