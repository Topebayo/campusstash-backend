import os
import uuid
import base64
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Body, status, File, UploadFile, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload

from pydantic import ValidationError
from app.api.deps import get_db, get_current_active_user
from app.models.user import User
from app.models.marketplace import Listing, ListingImage
from app.schemas.marketplace import (
    ListingCreate,
    ListingUpdate,
    ListingRead,
    ListingImageRead,
    ListingImageCreate,
    SellerProfileRead,
    PaginatedListingsResponse,
    UploadUrlRequest,
    UploadUrlResponse
)
from app.services.storage_service import generate_signed_upload_url

router = APIRouter()

def format_listing_read(listing: Listing) -> ListingRead:
    """Format SQLAlchemy Listing model into typed Pydantic ListingRead schema."""
    handle = None
    avatar = None
    if listing.seller and listing.seller.profile:
        handle = listing.seller.profile.burner_handle
        avatar = listing.seller.profile.avatar_slug

    seller_profile = None
    if listing.seller:
        seller_profile = SellerProfileRead(
            id=listing.seller.id,
            email=listing.seller.email,
            name=listing.seller.name,
            phone=listing.seller.phone,
            burner_handle=handle,
            avatar_slug=avatar
        )

    images_list = []
    if listing.images:
        for img in listing.images:
            images_list.append(
                ListingImageRead(
                    id=img.id,
                    listing_id=img.listing_id,
                    image_url=img.image_url,
                    is_primary=img.is_primary,
                    created_at=img.created_at
                )
            )

    # Use first image as primary fallback if image_url is unset
    primary_img = listing.image_url
    if not primary_img and images_list:
        primary_item = next((img for img in images_list if img.is_primary), images_list[0])
        primary_img = primary_item.image_url

    return ListingRead(
        id=listing.id,
        seller_id=listing.seller_id,
        title=listing.title,
        description=listing.description,
        price=listing.price,
        price_kobo=listing.price,
        category=listing.category or "Other",
        status=listing.status or "active",
        is_active=(listing.status == "active"),
        image_url=primary_img,
        created_at=listing.created_at,
        updated_at=listing.updated_at,
        seller=seller_profile,
        seller_handle=handle,
        images=images_list
    )


@router.get("", response_model=PaginatedListingsResponse)
@router.get("/", response_model=PaginatedListingsResponse)
async def list_listings(
    db: AsyncSession = Depends(get_db),
    category: Optional[str] = Query(None, description="Filter items by category (e.g. Textbooks, Electronics)"),
    min_price: Optional[int] = Query(None, ge=0, description="Minimum price filter (in kobo/minor units)"),
    max_price: Optional[int] = Query(None, ge=0, description="Maximum price filter (in kobo/minor units)"),
    search: Optional[str] = Query(None, description="Keyword search query matching title or description"),
    q: Optional[str] = Query(None, description="Alias for search query"),
    sort: Optional[str] = Query("newest", description="Sort order: newest, price_asc, price_desc"),
    status: Optional[str] = Query("active", description="Filter by status (default: 'active', pass 'all' for all)"),
    limit: int = Query(20, ge=1, le=100, description="Items per page (max: 100)"),
    offset: int = Query(0, ge=0, description="Pagination offset")
) -> Any:
    """
    List marketplace items with comprehensive filtering by category,
    price bounds, search keywords, and pagination (limit, offset).
    """
    base_conditions = []

    # Status filter
    if status and status.lower() != "all":
        base_conditions.append(Listing.status == status)

    # Category filter
    if category and category.strip() and category.lower() != "all":
        base_conditions.append(Listing.category.ilike(category.strip()))

    # Price range filters
    if min_price is not None:
        base_conditions.append(Listing.price >= min_price)
    if max_price is not None:
        base_conditions.append(Listing.price <= max_price)

    # Search filter (matches search or q parameter)
    search_term = (search or q or "").strip()
    if search_term:
        search_pattern = f"%{search_term}%"
        base_conditions.append(
            or_(
                Listing.title.ilike(search_pattern),
                Listing.description.ilike(search_pattern),
                Listing.category.ilike(search_pattern)
            )
        )

    # Compute total matching records
    count_stmt = select(func.count(Listing.id))
    if base_conditions:
        count_stmt = count_stmt.where(*base_conditions)
    total_res = await db.execute(count_stmt)
    total = total_res.scalar() or 0

    # Query paginated listings with eager-loaded seller and images
    query = (
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images)
        )
    )
    if base_conditions:
        query = query.where(*base_conditions)

        # Sorting
    if sort == "price_asc":
        query = query.order_by(Listing.price.asc(), Listing.id.desc())
    elif sort == "price_desc":
        query = query.order_by(Listing.price.desc(), Listing.id.desc())
    else:
        query = query.order_by(Listing.created_at.desc(), Listing.id.desc())

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    listings = result.scalars().all()

    items = [format_listing_read(l) for l in listings]
    has_more = (offset + limit) < total

    return PaginatedListingsResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more
    )


@router.post("", response_model=ListingRead, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=ListingRead, status_code=status.HTTP_201_CREATED)
async def create_listing(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Create a new marketplace listing. Requires authentication.
    Supports both standard JSON payload (`ListingCreate`) and multipart Form data.
    """
    content_type = request.headers.get("content-type", "")
    
    title: str = ""
    description: Optional[str] = None
    price: int = 0
    category: str = "Other"
    status_val: str = "active"
    primary_image_url: Optional[str] = None
    additional_images: List[str] = []

    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
        try:
            listing_in = ListingCreate.model_validate(body)
        except ValidationError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=e.errors())
        title = listing_in.title
        description = listing_in.description
        price = listing_in.price if listing_in.price is not None else (listing_in.price_kobo or 0)
        category = listing_in.category
        status_val = listing_in.status
        primary_image_url = listing_in.image_url
        additional_images = listing_in.images or []
    else:
        # Multipart form data
        form = await request.form()
        title = str(form.get("title", ""))
        description = str(form.get("description", "")) if form.get("description") else None
        price_raw = form.get("price") or form.get("price_kobo")
        if not price_raw:
            raise HTTPException(status_code=400, detail="Price is required")
        price = int(price_raw)
        category = str(form.get("category", "Other"))
        primary_image_url = form.get("image_url") or None
        
        # Check uploaded file if any - store persistently as base64 data URL to survive Render spin-down
        uploaded_file = form.get("image")
        if not primary_image_url and uploaded_file and hasattr(uploaded_file, "filename") and uploaded_file.filename:
            contents = await uploaded_file.read()
            mime = getattr(uploaded_file, "content_type", None) or "image/jpeg"
            b64_str = base64.b64encode(contents).decode("utf-8")
            primary_image_url = f"data:{mime};base64,{b64_str}"

    if not title or len(title.strip()) < 3:
        raise HTTPException(status_code=400, detail="Title must be at least 3 characters")

    listing = Listing(
        seller_id=current_user.id,
        title=title.strip(),
        description=description.strip() if description else None,
        price=price,
        category=category or "Other",
        status=status_val or "active",
        image_url=primary_image_url
    )
    db.add(listing)
    await db.flush()

    # Add primary image record if given
    if primary_image_url:
        img_rec = ListingImage(
            listing_id=listing.id,
            image_url=primary_image_url,
            is_primary=True
        )
        db.add(img_rec)

    # Add additional image records if given
    for img_url in additional_images:
        if img_url != primary_image_url:
            db.add(ListingImage(
                listing_id=listing.id,
                image_url=img_url,
                is_primary=False
            ))

    await db.commit()
    
    # Reload listing with full relationships
    result = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images)
        )
        .where(Listing.id == listing.id)
    )
    full_listing = result.scalar_one()
    return format_listing_read(full_listing)


@router.post("/{listing_id}/upload-url", response_model=UploadUrlResponse)
async def generate_upload_url(
    listing_id: uuid.UUID,
    payload: UploadUrlRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Generate a signed direct upload URL (for Cloudinary or AWS S3)
    allowing the frontend to upload product images directly to cloud storage.
    Automatically prepares the image record.
    """
    # Verify listing exists and user is owner
    result = await db.execute(
        select(Listing).where(Listing.id == listing_id)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.seller_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Not authorized to upload images for this listing"
        )

    # Generate signed upload URL and parameters
    upload_url, public_url, method, fields, headers = generate_signed_upload_url(
        listing_id=listing_id,
        filename=payload.filename,
        content_type=payload.content_type
    )

    # Save ListingImage record in database
    new_image = ListingImage(
        listing_id=listing_id,
        image_url=public_url,
        is_primary=payload.is_primary
    )
    db.add(new_image)
    if payload.is_primary or not listing.image_url:
        listing.image_url = public_url
    await db.commit()

    return UploadUrlResponse(
        upload_url=upload_url,
        public_url=public_url,
        provider="s3" if "s3" in upload_url else "cloudinary",
        method=method,
        fields=fields,
        headers=headers,
        expires_in=3600,
        listing_id=listing_id
    )


@router.post("/{listing_id}/images", response_model=ListingRead)
async def add_listing_image(
    listing_id: uuid.UUID,
    image_in: ListingImageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Attach an uploaded image URL directly to a listing.
    """
    result = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images)
        )
        .where(Listing.id == listing_id)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.seller_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if image_in.is_primary:
        # Reset previous primary flags
        for existing in listing.images:
            existing.is_primary = False
        listing.image_url = image_in.image_url

    new_img = ListingImage(
        listing_id=listing.id,
        image_url=image_in.image_url,
        is_primary=image_in.is_primary
    )
    db.add(new_img)
    await db.commit()
    await db.refresh(listing)
    return format_listing_read(listing)


@router.get("/me", response_model=List[ListingRead])
async def read_my_listings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    List all items posted by the currently authenticated user.
    """
    result = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images)
        )
        .where(Listing.seller_id == current_user.id)
        .order_by(Listing.created_at.desc())
    )
    listings = result.scalars().all()
    return [format_listing_read(l) for l in listings]


@router.get("/{listing_id}", response_model=ListingRead)
async def read_listing(
    listing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Retrieve single listing details including seller profile and all associated images.
    """
    result = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images)
        )
        .where(Listing.id == listing_id)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return format_listing_read(listing)


@router.patch("/{listing_id}/status", response_model=ListingRead)
async def toggle_listing_status(
    listing_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Update listing status (active, sold, etc.).
    """
    result = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images)
        )
        .where(Listing.id == listing_id)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.seller_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    status_str = body.get("status") or body.get("status_str")
    is_active = body.get("is_active")

    if status_str:
        listing.status = status_str
    elif is_active is not None:
        listing.status = "active" if is_active else "sold"
    else:
        listing.status = "sold" if listing.status == "active" else "active"

    await db.commit()
    await db.refresh(listing)
    return format_listing_read(listing)


@router.delete("/{listing_id}")
async def delete_listing(
    listing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Delete a listing. Cascades to images, conversations, and messages.
    """
    result = await db.execute(select(Listing).where(Listing.id == listing_id))
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.seller_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    await db.delete(listing)
    await db.commit()
    return {"detail": "Listing deleted successfully"}


@router.put("/{listing_id}", response_model=ListingRead)
@router.patch("/{listing_id}", response_model=ListingRead)
async def update_listing(
    listing_id: uuid.UUID,
    listing_in: ListingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Update listing details (title, description, price, category, image_url, status).
    """
    result = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images)
        )
        .where(Listing.id == listing_id)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.seller_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to edit this listing")

    if listing_in.title and len(listing_in.title.strip()) >= 1:
        listing.title = listing_in.title.strip()
    if listing_in.description is not None:
        listing.description = listing_in.description.strip()
    if listing_in.price is not None and listing_in.price >= 0:
        listing.price = listing_in.price
    if listing_in.category:
        listing.category = listing_in.category
    if listing_in.image_url:
        listing.image_url = listing_in.image_url
    if listing_in.status:
        listing.status = listing_in.status

    await db.commit()
    await db.refresh(listing)
    return format_listing_read(listing)
