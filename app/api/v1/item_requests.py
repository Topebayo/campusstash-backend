import uuid
from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, get_current_active_user
from app.models.user import User
from app.models.campus import ItemRequest, Campus
from app.schemas.item_request import ItemRequestCreate, ItemRequestRead
from app.core.limiter import limiter

router = APIRouter()

def format_request_read(req: ItemRequest) -> ItemRequestRead:
    handle = None
    avatar = None
    if req.user and req.user.profile:
        handle = req.user.profile.burner_handle
        avatar = req.user.profile.avatar_slug

    campus_name = req.campus.name if req.campus else None
    campus_acronym = req.campus.acronym if req.campus else None

    return ItemRequestRead(
        id=req.id,
        user_id=req.user_id,
        campus_id=req.campus_id,
        title=req.title,
        budget_kobo=req.budget_kobo,
        description=req.description,
        is_fulfilled=req.is_fulfilled,
        created_at=req.created_at,
        updated_at=req.updated_at,
        requester_handle=handle,
        requester_avatar=avatar,
        campus_name=campus_name,
        campus_acronym=campus_acronym
    )

@router.get("", response_model=List[ItemRequestRead])
@router.get("/", response_model=List[ItemRequestRead])
async def list_item_requests(
    campus_id: Optional[uuid.UUID] = Query(None, description="Filter by university campus"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    List active student item requests (Wanted Board).
    Publicly accessible to unauthenticated guests and students.
    """
    query = (
        select(ItemRequest)
        .options(
            selectinload(ItemRequest.user).selectinload(User.profile),
            selectinload(ItemRequest.campus)
        )
        .where(ItemRequest.is_fulfilled == False)
    )

    if campus_id:
        query = query.where(ItemRequest.campus_id == campus_id)

    query = query.order_by(ItemRequest.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    requests = result.scalars().all()
    return [format_request_read(r) for r in requests]

@router.post("", response_model=ItemRequestRead, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=ItemRequestRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_item_request(
    request: Request,
    req_in: ItemRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Post a new item request to the Wanted Board.
    Requires authentication.
    """
    # Default to user's registered campus if not provided in payload
    target_campus_id = req_in.campus_id or current_user.campus_id

    item_req = ItemRequest(
        user_id=current_user.id,
        campus_id=target_campus_id,
        title=req_in.title.strip(),
        budget_kobo=req_in.budget_kobo,
        description=req_in.description.strip() if req_in.description else None,
        is_fulfilled=False
    )
    db.add(item_req)
    await db.commit()

    refetch = await db.execute(
        select(ItemRequest)
        .options(
            selectinload(ItemRequest.user).selectinload(User.profile),
            selectinload(ItemRequest.campus)
        )
        .where(ItemRequest.id == item_req.id)
    )
    saved = refetch.scalar_one()
    return format_request_read(saved)

@router.patch("/{request_id}/fulfill", response_model=ItemRequestRead)
async def mark_request_fulfilled(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Mark an item request as fulfilled/completed. Owner only.
    """
    result = await db.execute(
        select(ItemRequest)
        .options(
            selectinload(ItemRequest.user).selectinload(User.profile),
            selectinload(ItemRequest.campus)
        )
        .where(ItemRequest.id == request_id)
    )
    item_req = result.scalar_one_or_none()
    if not item_req:
        raise HTTPException(status_code=404, detail="Request not found")
    if item_req.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    item_req.is_fulfilled = True
    await db.commit()
    await db.refresh(item_req)
    return format_request_read(item_req)

@router.delete("/{request_id}")
async def delete_item_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> Any:
    """
    Delete an item request. Owner only.
    """
    result = await db.execute(select(ItemRequest).where(ItemRequest.id == request_id))
    item_req = result.scalar_one_or_none()
    if not item_req:
        raise HTTPException(status_code=404, detail="Request not found")
    if item_req.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    await db.delete(item_req)
    await db.commit()
    return {"detail": "Request deleted successfully"}
