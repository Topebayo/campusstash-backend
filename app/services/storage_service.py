import os
import time
import uuid
import hashlib
from typing import Dict, Any, Tuple, Optional
import cloudinary.utils
try:
    import boto3
    from botocore.config import Config
except ImportError:
    boto3 = None
    Config = None

from app.core.config import settings

def is_cloudinary_configured() -> bool:
    """
    Check if valid live Cloudinary credentials are set in configuration.
    """
    return bool(
        settings.CLOUDINARY_CLOUD_NAME
        and settings.CLOUDINARY_API_KEY
        and settings.CLOUDINARY_API_SECRET
        and settings.CLOUDINARY_API_KEY != "123456789012345"
        and "sample" not in str(settings.CLOUDINARY_API_SECRET)
    )

def upload_image_to_storage(
    file_or_data: Any,
    folder: str = "campusstash/listings",
    public_id: Optional[str] = None
) -> Optional[str]:
    """
    Directly upload an image (bytes, file, or base64 data URL) to Cloudinary.
    Returns the secure CDN URL or None if not configured or failed.
    """
    if not is_cloudinary_configured():
        return None

    try:
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True
        )
        res = cloudinary.uploader.upload(
            file_or_data,
            folder=folder,
            public_id=public_id,
            resource_type="image",
            overwrite=True
        )
        return res.get("secure_url") or res.get("url")
    except Exception as e:
        print(f"[Cloudinary] Upload failed: {e}")
        return None

def generate_signed_upload_url(
    listing_id: uuid.UUID,
    filename: str,
    content_type: str = "image/jpeg",
    provider: str | None = None
) -> Tuple[str, str, str, Dict[str, Any] | None, Dict[str, str] | None]:
    """
    Generate signed direct upload URL for frontend direct-to-cloud upload.
    Returns: (upload_url, public_url, method, fields, headers)
    """
    provider = provider or settings.STORAGE_PROVIDER

    if provider == "s3" and settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        # Generate AWS S3 Presigned PUT URL
        file_ext = os.path.splitext(filename)[1] or ".jpg"
        clean_name = f"{uuid.uuid4().hex[:12]}{file_ext}"
        s3_key = f"listings/{listing_id}/{clean_name}"
        
        s3_client = boto3.client(
            "s3",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4")
        )
        
        presigned_url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": settings.S3_BUCKET_NAME,
                "Key": s3_key,
                "ContentType": content_type
            },
            ExpiresIn=3600
        )
        public_url = f"https://{settings.S3_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{s3_key}"
        headers = {"Content-Type": content_type}
        return presigned_url, public_url, "PUT", None, headers

    else:
        if not is_cloudinary_configured():
            raise ValueError("Cloudinary storage is not configured. Please set CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET in .env.")
        # Default / Cloudinary signed direct upload
        timestamp = int(time.time())
        folder = f"campusstash/listings/{listing_id}"
        public_id = f"img_{uuid.uuid4().hex[:12]}"
        
        params_to_sign = {
            "folder": folder,
            "public_id": public_id,
            "timestamp": timestamp
        }
        
        # Sign parameters with API Secret
        signature = cloudinary.utils.api_sign_request(
            params_to_sign, 
            settings.CLOUDINARY_API_SECRET
        )
        
        upload_url = f"https://api.cloudinary.com/v1_1/{settings.CLOUDINARY_CLOUD_NAME}/image/upload"
        public_url = f"https://res.cloudinary.com/{settings.CLOUDINARY_CLOUD_NAME}/image/upload/{folder}/{public_id}"
        
        fields = {
            "api_key": settings.CLOUDINARY_API_KEY,
            "timestamp": timestamp,
            "folder": folder,
            "public_id": public_id,
            "signature": signature
        }
        return upload_url, public_url, "POST", fields, None
