import asyncio
from app.db.session import AsyncSessionLocal
from app.models.user import User
from sqlalchemy import select

async def get_users():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User.email))
        emails = res.scalars().all()
        for e in emails:
            print(f'Email: {e}')

asyncio.run(get_users())
