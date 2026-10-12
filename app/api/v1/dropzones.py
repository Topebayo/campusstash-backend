import uuid
from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db, get_current_active_user
from app.models.user import User
from app.models.marketplace import SafeDropZone
from app.schemas.marketplace import SafeDropZoneCreate, SafeDropZoneRead

router = APIRouter()

DEFAULT_DROPZONES = [
    {"name": "Main Library Entrance", "location_details": "Outside the central library main steps, well-lit CCTV zone"},
    {"name": "Student Union Building (SUB) Plaza", "location_details": "Central SUB fountain court and cafe patio"},
    {"name": "Hostel Hall A / B Common Room", "location_details": "Ground floor security desk & reception area"},
    {"name": "Faculty Cafeteria & Food Court", "location_details": "Central dining area, high student traffic"},
    {"name": "Sports Complex & Gym Gate", "location_details": "Main gymnasium entrance gate & benches"},
    {"name": "Campus Main Security Outpost", "location_details": "24/7 security booth near main campus gates"}
]

@router.post("/", response_model=SafeDropZoneRead)
async def create_dropzone(
    *,
    db: AsyncSession = Depends(get_db),
    dropzone_in: SafeDropZoneCreate,
    current_user: User = Depends(get_current_active_user)
) -> Any:
    dropzone = SafeDropZone(
        name=dropzone_in.name,
        location_details=dropzone_in.location_details
    )
    db.add(dropzone)
    await db.commit()
    await db.refresh(dropzone)
    return dropzone

@router.get("/", response_model=List[SafeDropZoneRead])
async def read_dropzones(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 100
) -> Any:
    result = await db.execute(select(SafeDropZone).offset(skip).limit(limit))
    dropzones = result.scalars().all()
    if not dropzones:
        # Auto-seed default campus safe dropzones
        for dz in DEFAULT_DROPZONES:
            db.add(SafeDropZone(name=dz["name"], location_details=dz["location_details"]))
        await db.commit()
        res_fresh = await db.execute(select(SafeDropZone).offset(skip).limit(limit))
        dropzones = res_fresh.scalars().all()
    return dropzones
