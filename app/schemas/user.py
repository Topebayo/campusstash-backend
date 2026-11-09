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
    account_type: Optional[str] = Field("individual", description="'individual' or 'store'")
    business_name: Optional[str] = None
    business_category: Optional[str] = None
    business_description: Optional[str] = None
    business_address: Optional[str] = None
    business_whatsapp: Optional[str] = None
    business_instagram: Optional[str] = None
    campus_id: Optional[uuid.UUID] = None
    profile: Optional[PublicProfileCreate] = None

# Backward compatibility alias
UserCreate = UserRegister


class SendOtpRequest(BaseModel):
    phone: Optional[str] = Field(None, description="Phone number to receive OTP (e.g. 08012345678 or +234...)")
    email: Optional[EmailStr] = Field(None, description="Student email to receive OTP")
    channel: Optional[str] = Field("sms", description="Delivery channel: 'sms', 'whatsapp', or 'email'")

class VerifyOtpRequest(BaseModel):
    phone: Optional[str] = Field(None, description="Phone number being verified")
    email: Optional[EmailStr] = Field(None, description="Student email being verified")
    otp_code: str = Field(..., min_length=4, max_length=10, description="Verification code received")

class ForgotPasswordSendOtpRequest(BaseModel):
    phone: Optional[str] = Field(None, description="Registered phone number or email for account recovery")
    email: Optional[EmailStr] = Field(None, description="Registered email address for account recovery")
    channel: Optional[str] = Field("sms", description="Delivery channel: 'sms', 'whatsapp', or 'email'")

class ForgotPasswordResetRequest(BaseModel):
    phone: Optional[str] = Field(None, description="Registered phone number")
    email: Optional[EmailStr] = Field(None, description="Registered email address")
    otp_code: str = Field(..., min_length=4, max_length=10, description="Verification code received")
    new_password: str = Field(..., min_length=8, description="New password (minimum 8 characters)")

class OtpResponse(BaseModel):
    status: str
    message: str
    phone: Optional[str] = None
    email: Optional[str] = None
    dev_code: Optional[str] = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserRead(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: Optional[str] = None
    phone: Optional[str] = None
    is_active: bool = True
    is_phone_verified: bool = False
    account_type: str = "individual"
    business_name: Optional[str] = None
    business_category: Optional[str] = None
    business_description: Optional[str] = None
    business_address: Optional[str] = None
    business_whatsapp: Optional[str] = None
    business_instagram: Optional[str] = None
    kyc_status: str = "unverified"
    kyc_user_type: str = "student"
    kyc_id_type: Optional[str] = None
    kyc_id_number: Optional[str] = None
    kyc_student_id: Optional[str] = None
    kyc_id_card_url: Optional[str] = None
    kyc_selfie_url: Optional[str] = None
    kyc_submitted_at: Optional[datetime] = None
    kyc_verified_at: Optional[datetime] = None
    campus_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    profile: Optional[PublicProfileRead] = None
    
    model_config = ConfigDict(from_attributes=True)

class UserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    burner_handle: Optional[str] = None
    account_type: Optional[str] = None
    business_name: Optional[str] = None
    business_category: Optional[str] = None
    business_description: Optional[str] = None
    business_address: Optional[str] = None
    business_whatsapp: Optional[str] = None
    business_instagram: Optional[str] = None
    kyc_status: Optional[str] = None
    kyc_user_type: Optional[str] = None
    kyc_id_type: Optional[str] = None
    kyc_id_number: Optional[str] = None
    kyc_student_id: Optional[str] = None
    kyc_id_card_url: Optional[str] = None
    kyc_selfie_url: Optional[str] = None
    campus_id: Optional[uuid.UUID] = None

class GoogleAuthRequest(BaseModel):
    credential: Optional[str] = Field(None, description="Google ID Token JWT from Google Identity Services")
    email: Optional[EmailStr] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    google_id: Optional[str] = None
    campus_id: Optional[uuid.UUID] = None

class StoreKycRequest(BaseModel):
    business_name: str = Field(..., min_length=2, max_length=255)
    business_category: str = Field(..., min_length=2, max_length=100)
    user_type: str = Field("student", description="'student' or 'non_student'")
    student_id_number: Optional[str] = None
    id_type: Optional[str] = Field("student_id", description="student_id, nin, voters_card, drivers_license, passport, national_id")
    id_number: Optional[str] = None
    business_description: Optional[str] = None
    business_address: Optional[str] = None
    business_whatsapp: Optional[str] = None
    business_instagram: Optional[str] = None
    id_card_image: Optional[str] = None
    face_selfie_image: Optional[str] = None

class InAppVerifyPhoneRequest(BaseModel):
    phone: str = Field(..., min_length=7, max_length=30)
    otp_code: str = Field(..., min_length=4, max_length=10)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class StoreSummary(BaseModel):
    id: uuid.UUID
    name: Optional[str] = None
    email: Optional[str] = None
    burner_handle: Optional[str] = None
    avatar_slug: Optional[str] = None
    account_type: str = "store"
    kyc_status: str = "unverified"
    business_name: Optional[str] = None
    business_category: Optional[str] = None
    business_description: Optional[str] = None
    business_address: Optional[str] = None
    business_whatsapp: Optional[str] = None
    business_instagram: Optional[str] = None
    campus_id: Optional[uuid.UUID] = None
    campus_name: Optional[str] = None
    campus_acronym: Optional[str] = None
    listings_count: int = 0
    sample_images: list[str] = []

    model_config = ConfigDict(from_attributes=True)
