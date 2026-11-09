import os
import logging
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("campusstash.security")

class Settings(BaseSettings):
    PROJECT_NAME: str = "CampusStash"
    ENVIRONMENT: str = "development"  # "development", "staging", "production"
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./campusstash.db"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    
    # Security & Tokens
    JWT_SECRET: str = "super_secret_jwt_key_change_in_production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30             # 30 days
    ALLOWED_ORIGINS: str = ""                       # Comma-separated extra origins
    
    # Third Party Secrets (Saved in server-side .env, NEVER exposed to client)
    PAYSTACK_SECRET: str = "sk_test_12345"

    # Cloud Storage & Uploads
    STORAGE_PROVIDER: str = "cloudinary"  # "cloudinary" or "s3"
    
    # Cloudinary Config
    CLOUDINARY_CLOUD_NAME: Optional[str] = "campusstash"
    CLOUDINARY_API_KEY: Optional[str] = None
    CLOUDINARY_API_SECRET: Optional[str] = None
    
    # AWS S3 Config
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = "campusstash-uploads"

    # Resend Email OTP
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "CampusStash <auth@salesaiflow.online>"

    # Termii SMS & WhatsApp OTP
    ENABLE_LIVE_SMS: bool = True  # Set to True when ready to burn live Termii credits
    TERMII_API_KEY: str = ""
    TERMII_SENDER_ID: str = "OE Alert"
    TERMII_CHANNEL: str = "dnd"
    TERMII_BASE_URL: str = "https://api.ng.termii.com"
    OTP_EXPIRE_MINUTES: int = 10
    DEV_BYPASS_OTP: Optional[str] = None
    ADMIN_BYPASS_OTP: str = "990011"
    ADMIN_BYPASS_PIN: str = "7799"
    ADMIN_EMAILS: str = "topebayo113@gmail.com,titantemitopebayoo@gmail.com,salesflowingai@gmail.com,admin@campusstash.ng"

    # Meta WhatsApp Cloud API
    WHATSAPP_VERIFY_TOKEN: str = "campusstash_meta_verify_2026"
    WHATSAPP_ACCESS_TOKEN: Optional[str] = None
    WHATSAPP_PHONE_NUMBER_ID: Optional[str] = None
    WHATSAPP_BUSINESS_ACCOUNT_ID: Optional[str] = None
    ADMIN_WHATSAPP_NUMBERS: str = "2348000000000"  # Comma-separated authorized admin WhatsApp numbers

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    def get_cors_origins(self) -> List[str]:
        """
        Generate strict CORS allowed origins whitelist.
        Disallows wildcards (*) when credentials are used.
        """
        origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "capacitor://localhost",
            "https://localhost",
            "http://localhost",
            "https://127.0.0.1",
            "http://127.0.0.1",
            "ionic://localhost",
            "https://campusstash-frontend.onrender.com",
            "https://campusstash.onrender.com",
            "https://campusstash-frontend.vercel.app",
            "https://campusstash.vercel.app",
        ]
        if self.ALLOWED_ORIGINS:
            for org in self.ALLOWED_ORIGINS.split(","):
                org_clean = org.strip().rstrip("/")
                if org_clean and org_clean not in origins:
                    origins.append(org_clean)
        return origins

settings = Settings()

if settings.ENVIRONMENT == "production" and "super_secret_jwt_key" in settings.JWT_SECRET:
    logger.warning("CRITICAL SECURITY ALERT: Running in production with default JWT_SECRET! Set JWT_SECRET in server environment.")
