import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict, model_validator

class ItemRequestCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    budget: Optional[Any] = None
    budget_kobo: Optional[Any] = None
    description: Optional[str] = None
    campus_id: Optional[uuid.UUID] = None

    @model_validator(mode="before")
    @classmethod
    def resolve_budget(cls, values: Any) -> Any:
        if isinstance(values, dict):
            b = values.get("budget")
            bk = values.get("budget_kobo")
            val = bk if bk is not None else b
            if val is not None and val != "":
                try:
                    num = float(val)
                    # If budget was sent in Naira (< 100,000 and not ending in 00), convert to kobo
                    if bk is None and num > 0:
                        values["budget_kobo"] = int(round(num * 100))
                    else:
                        values["budget_kobo"] = int(round(num))
                except Exception:
                    values["budget_kobo"] = None
        return values

class ItemRequestRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    campus_id: Optional[uuid.UUID] = None
    title: str
    budget_kobo: Optional[int] = None
    description: Optional[str] = None
    is_fulfilled: bool = False
    created_at: datetime
    updated_at: datetime

    # Denormalized presenter data
    requester_handle: Optional[str] = None
    requester_avatar: Optional[str] = None
    campus_name: Optional[str] = None
    campus_acronym: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
