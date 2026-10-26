import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict

class CampusBase(BaseModel):
    name: str
    acronym: str
    state: str
    popular_hostels: Optional[List[str]] = []

class CampusCreate(CampusBase):
    pass

class CampusRead(CampusBase):
    id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
