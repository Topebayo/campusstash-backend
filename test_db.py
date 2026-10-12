import asyncio
from app.db.session import AsyncSessionLocal
from app.models.user import User
from sqlalchemy import select
async def test():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User))
        users = res.scalars().all()
        for u in users: print(u.email)
asyncio.run(test())
