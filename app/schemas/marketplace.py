import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, model_validator

class ListingImageBase(BaseModel):
    image_url: str
    is_primary: bool = False

class ListingImageCreate(ListingImageBase):
    pass

class ListingImageRead(ListingImageBase):
    id: uuid.UUID
    listing_id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class SellerProfileRead(BaseModel):
    id: uuid.UUID
    email: str
    name: Optional[str] = None
    phone: Optional[str] = None
    burner_handle: Optional[str] = None
    avatar_slug: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class ListingCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255, description="Item title")
    description: Optional[str] = Field(None, description="Detailed item description")
    price: Optional[Any] = Field(None, description="Price in kobo or Naira")
    price_kobo: Optional[Any] = Field(None, description="Price in kobo")
    category: str = Field(default="Other", max_length=50, description="Item category")
    status: str = Field(default="active", max_length=50, description="Status: active, sold, etc.")
    image_url: Optional[str] = Field(None, description="Optional primary cover image URL")
    images: Optional[List[str]] = Field(default_factory=list, description="List of image URLs to attach")
    campus_id: Optional[uuid.UUID] = Field(None, description="Optional Campus ID scope")

    @model_validator(mode="before")
    @classmethod
    def resolve_price(cls, values: Any) -> Any:
        if isinstance(values, dict):
            p = values.get("price")
            pk = values.get("price_kobo")
            val = pk if pk is not None else p
            if val is None:
                raise ValueError("Either price or price_kobo must be provided.")
            try:
                num = float(val)
                values["price"] = int(round(num))
                values["price_kobo"] = int(round(num))
            except (ValueError, TypeError):
                raise ValueError("Invalid numeric price provided.")
        return values

class ListingUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    price: Optional[Any] = None
    price_kobo: Optional[Any] = None
    category: Optional[str] = Field(None, max_length=50)
    status: Optional[str] = Field(None, max_length=50)
    image_url: Optional[str] = None
    images: Optional[List[str]] = None
    campus_id: Optional[uuid.UUID] = None

    @model_validator(mode="before")
    @classmethod
    def resolve_price(cls, values: Any) -> Any:
        if isinstance(values, dict):
            p = values.get("price")
            pk = values.get("price_kobo")
            val = pk if pk is not None else p
            if val is not None:
                try:
                    num = float(val)
                    values["price"] = int(round(num))
                    values["price_kobo"] = int(round(num))
                except (ValueError, TypeError):
                    pass
        return values

class ListingRead(BaseModel):
    id: uuid.UUID
    seller_id: uuid.UUID
    title: str
    description: Optional[str] = None
    price: int
    price_kobo: int
    category: str = "Other"
    status: str = "active"
    is_active: bool = True
    image_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    seller: Optional[SellerProfileRead] = None
    seller_handle: Optional[str] = None
    campus_id: Optional[uuid.UUID] = None
    campus_name: Optional[str] = None
    campus_acronym: Optional[str] = None
    images: List[ListingImageRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

class PaginatedListingsResponse(BaseModel):
    items: List[ListingRead]
    total: int
    limit: int
    offset: int
    has_more: bool

class UploadUrlRequest(BaseModel):
    filename: str = Field(..., description="Original image filename")
    content_type: str = Field(default="image/jpeg", description="MIME type")
    is_primary: bool = Field(default=False, description="Set this image as primary")

class UploadUrlResponse(BaseModel):
    upload_url: str = Field(..., description="Signed URL")
    public_url: str = Field(..., description="Publicly accessible URL")
    provider: str = Field(..., description="Storage provider")
    method: str = Field(default="POST", description="HTTP method")
    fields: Optional[Dict[str, Any]] = Field(None)
    headers: Optional[Dict[str, str]] = Field(None)
    expires_in: int = Field(default=3600)
    listing_id: uuid.UUID

class SafeDropZoneCreate(BaseModel):
    name: str
    location_details: Optional[str] = None

class SafeDropZoneRead(BaseModel):
    id: uuid.UUID
    name: str
    location_details: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)
