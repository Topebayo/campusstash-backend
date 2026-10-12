import asyncio
from app.db.session import AsyncSessionLocal
from app.models.user import PublicProfile
from sqlalchemy import select
async def test():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(PublicProfile))
        profiles = res.scalars().all()
        for p in profiles: print(p.burner_handle)
asyncio.run(test())
