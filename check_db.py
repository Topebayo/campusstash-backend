import asyncio
from app.db.session import AsyncSessionLocal
from app.models.marketplace import Listing
from sqlalchemy import select

async def check():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Listing).order_by(Listing.id.desc()).limit(1))
        listing = res.scalars().first()
        if listing:
            print(f'Latest Listing: {listing.title}')
            print(f'Image URL: {listing.image_url}')

asyncio.run(check())
