import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "CampusStash"
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./campusstash.db"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    
    # Security
    JWT_SECRET: str = "super_secret_jwt_key_change_in_production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30             # 30 days
    
    # Third Party
    PAYSTACK_SECRET: str = "sk_test_12345"

    # Cloud Storage & Uploads
    STORAGE_PROVIDER: str = "cloudinary"  # "cloudinary" or "s3"
    
    # Cloudinary Config
    CLOUDINARY_CLOUD_NAME: str = "campusstash"
    CLOUDINARY_API_KEY: str = "123456789012345"
    CLOUDINARY_API_SECRET: str = "sample_cloudinary_secret_key"
    
    # AWS S3 Config
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = "campusstash-uploads"

    # Termii SMS & WhatsApp OTP
    TERMII_API_KEY: str = ""
    TERMII_SENDER_ID: str = "CampusStash"
    TERMII_CHANNEL: str = "generic"
    TERMII_BASE_URL: str = "https://api.ng.termii.com"
    OTP_EXPIRE_MINUTES: int = 10
    DEV_BYPASS_OTP: str = "123456"


    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

settings = Settings()
