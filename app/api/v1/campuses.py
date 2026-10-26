import uuid
from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.api.deps import get_db
from app.models.campus import Campus
from app.schemas.campus import CampusRead, CampusCreate

router = APIRouter()

@router.get("", response_model=List[CampusRead])
@router.get("/", response_model=List[CampusRead])
async def list_campuses(
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    List all supported campuses with acronyms, states, and popular hostels.
    Publicly accessible - no authentication required.
    """
    result = await db.execute(select(Campus).order_by(Campus.name.asc()))
    return result.scalars().all()

@router.post("", response_model=CampusRead, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=CampusRead, status_code=status.HTTP_201_CREATED)
async def create_campus(
    campus_in: CampusCreate,
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Add a new university campus.
    """
    existing = await db.execute(select(Campus).where(Campus.name == campus_in.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Campus already exists")

    campus = Campus(
        name=campus_in.name.strip(),
        acronym=campus_in.acronym.strip().upper(),
        state=campus_in.state.strip(),
        popular_hostels=campus_in.popular_hostels or []
    )
    db.add(campus)
    await db.commit()
    await db.refresh(campus)
    return campus
