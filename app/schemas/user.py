import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict

class PublicProfileCreate(BaseModel):
    burner_handle: str
    avatar_slug: Optional[str] = None

class PublicProfileRead(BaseModel):
    burner_handle: str
    avatar_slug: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, description="Password (at least 6 characters)")
    name: Optional[str] = None
    phone: Optional[str] = None
    burner_handle: Optional[str] = None
    profile: Optional[PublicProfileCreate] = None

# Backward compatibility alias
UserCreate = UserRegister

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserRead(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: Optional[str] = None
    phone: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    profile: Optional[PublicProfileRead] = None
    
    model_config = ConfigDict(from_attributes=True)

class UserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    burner_handle: Optional[str] = None
