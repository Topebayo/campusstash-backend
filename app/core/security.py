from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from passlib.context import CryptContext
from jose import jwt, JWTError, ExpiredSignatureError

from app.core.config import settings

# CryptContext configured with bcrypt algorithm
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a raw password against the stored bcrypt hash.
    Bcrypt inherently caps input at 72 bytes.
    """
    if not plain_password or not hashed_password:
        return False
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """
    Generate a secure bcrypt password hash.
    """
    return pwd_context.hash(password)

def create_access_token(
    subject: str | Any, 
    expires_delta: timedelta | None = None,
    extra_claims: Dict[str, Any] | None = None
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
    expires_delta: timedelta | None = None
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
