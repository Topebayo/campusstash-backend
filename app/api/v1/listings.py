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
from app.core.limiter import limiter

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

    campus_name = listing.campus.name if listing.campus else None
    campus_acronym = listing.campus.acronym if listing.campus else None

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
        campus_id=listing.campus_id,
        campus_name=campus_name,
        campus_acronym=campus_acronym,
        images=images_list
    )


@router.get("", response_model=PaginatedListingsResponse)
@router.get("/", response_model=PaginatedListingsResponse)
async def list_listings(
    db: AsyncSession = Depends(get_db),
    campus_id: Optional[uuid.UUID] = Query(None, description="Filter items by university campus"),
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

    # Campus filter
    if campus_id is not None:
        base_conditions.append(Listing.campus_id == campus_id)

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
            selectinload(Listing.images),
            selectinload(Listing.campus)
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
@limiter.limit("10/minute")
async def create_listing(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Create a new marketplace listing. Requires authentication.
    Supports both standard JSON payload (ListingCreate) and multipart Form data.
    Rate limited to 10 listings per minute.
    """
    content_type = request.headers.get("content-type", "")
    
    title: str = ""
    description: Optional[str] = None
    price: int = 0
    category: str = "Other"
    status_val: str = "active"
    primary_image_url: Optional[str] = None
    additional_images: List[str] = []
    target_campus_id: Optional[uuid.UUID] = current_user.campus_id

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
        if listing_in.campus_id:
            target_campus_id = listing_in.campus_id
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
        campus_raw = form.get("campus_id")
        if campus_raw and str(campus_raw).strip():
            try:
                target_campus_id = uuid.UUID(str(campus_raw).strip())
            except Exception:
                pass
        
        # Check primary uploaded file if any - store persistently as base64 data URL to survive Render spin-down
        uploaded_file = form.get("image")
        if not primary_image_url and uploaded_file and hasattr(uploaded_file, "filename") and uploaded_file.filename:
            contents = await uploaded_file.read()
            mime = getattr(uploaded_file, "content_type", None) or "image/jpeg"
            b64_str = base64.b64encode(contents).decode("utf-8")
            primary_image_url = f"data:{mime};base64,{b64_str}"

        # Check secondary / detail image if provided in form
        secondary_image_url = form.get("image_2_url") or form.get("secondary_image_url") or None
        uploaded_file_2 = form.get("image_2") or form.get("secondary_image")
        if not secondary_image_url and uploaded_file_2 and hasattr(uploaded_file_2, "filename") and uploaded_file_2.filename:
            contents_2 = await uploaded_file_2.read()
            mime_2 = getattr(uploaded_file_2, "content_type", None) or "image/jpeg"
            b64_str_2 = base64.b64encode(contents_2).decode("utf-8")
            secondary_image_url = f"data:{mime_2};base64,{b64_str_2}"
        if secondary_image_url:
            additional_images.append(secondary_image_url)

    if not title or len(title.strip()) < 3:
        raise HTTPException(status_code=400, detail="Title must be at least 3 characters")

    listing = Listing(
        seller_id=current_user.id,
        campus_id=target_campus_id,
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

    # Add any additional secondary images
    for add_url in additional_images:
        if add_url and add_url != primary_image_url:
            db.add(ListingImage(
                listing_id=listing.id,
                image_url=add_url,
                is_primary=False
            ))

    await db.commit()

    # Re-fetch listing with relationships loaded
    result = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images),
            selectinload(Listing.campus)
        )
        .where(Listing.id == listing.id)
    )
    full_listing = result.scalar_one()
    return format_listing_read(full_listing)


@router.post("/{listing_id}/upload-url", response_model=UploadUrlResponse)
@limiter.limit("20/minute")
async def generate_upload_url(
    request: Request,
    listing_id: uuid.UUID,
    payload: UploadUrlRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Generate a signed direct upload URL (for Cloudinary or AWS S3)
    allowing the frontend to upload product images directly to cloud storage.
    Strict Row-Level Security: Only the listing owner can generate upload URLs.
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
@limiter.limit("20/minute")
async def add_listing_image(
    request: Request,
    listing_id: uuid.UUID,
    image_in: ListingImageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Attach an uploaded image URL directly to a listing.
    Strict Row-Level Security: Only the listing owner can attach images.
    """
    result = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images),
            selectinload(Listing.campus)
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
    if listing.images is None:
        listing.images = []
    listing.images.append(new_img)
    db.add(new_img)
    await db.commit()
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
            selectinload(Listing.images),
            selectinload(Listing.campus)
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
            selectinload(Listing.images),
            selectinload(Listing.campus)
        )
        .where(Listing.id == listing_id)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return format_listing_read(listing)


@router.patch("/{listing_id}/status", response_model=ListingRead)
@limiter.limit("15/minute")
async def toggle_listing_status(
    listing_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Update listing status (active, sold, etc.).
    Strict Row-Level Security: Only the listing seller can modify status.
    """
    result = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images),
            selectinload(Listing.campus)
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
    refetch_res = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images),
            selectinload(Listing.campus)
        )
        .where(Listing.id == listing_id)
    )
    full_listing = refetch_res.scalar_one()
    return format_listing_read(full_listing)


@router.delete("/{listing_id}")
@limiter.limit("15/minute")
async def delete_listing(
    request: Request,
    listing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Delete a listing. Cascades to images, conversations, and messages.
    Strict Row-Level Security: Only the listing seller can delete it.
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
@limiter.limit("15/minute")
async def update_listing(
    request: Request,
    listing_id: uuid.UUID,
    listing_in: ListingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Update listing details (title, description, price, category, image_url, status).
    Strict Row-Level Security: Only the listing seller can update details.
    """
    result = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images),
            selectinload(Listing.campus)
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
    if listing_in.price_kobo is not None:
        listing.price = listing_in.price_kobo
    elif listing_in.price is not None and listing_in.price >= 0:
        listing.price = listing_in.price
    if listing_in.category:
        listing.category = listing_in.category
    if listing_in.campus_id is not None:
        listing.campus_id = listing_in.campus_id
    if listing_in.image_url:
        listing.image_url = listing_in.image_url
    if listing_in.status:
        listing.status = listing_in.status
    if listing_in.images is not None:
        target_primary = listing_in.image_url or listing.image_url
        new_img_objs = []
        if target_primary:
            new_img_objs.append(ListingImage(listing_id=listing.id, image_url=target_primary, is_primary=True))
        for img_url in listing_in.images:
            if img_url and img_url != target_primary:
                new_img_objs.append(ListingImage(listing_id=listing.id, image_url=img_url, is_primary=False))
        listing.images = new_img_objs

    await db.commit()
    refetch_res = await db.execute(
        select(Listing)
        .options(
            selectinload(Listing.seller).selectinload(User.profile),
            selectinload(Listing.images),
            selectinload(Listing.campus)
        )
        .where(Listing.id == listing_id)
    )
    full_listing = refetch_res.scalar_one()
    return format_listing_read(full_listing)
