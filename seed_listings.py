import asyncio
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.marketplace import Listing
from sqlalchemy import select

async def seed():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User))
        user = res.scalars().first()
        if not user:
            print('No user found!')
            return

        items = [
            ('MacBook Pro M2', 'Used for 1 year, mint condition.', 150000000),
            ('Calculus Textbook', 'Slightly worn, 8th edition.', 1500000),
            ('AirPods Pro', 'Lost the case, right earbud only.', 2500000),
            ('Mini Fridge', 'Perfect for dorm rooms. Very cold.', 4500000),
            ('Desk Lamp', 'LED with adjustable brightness.', 500000),
            ('Ergonomic Chair', 'Saves your back during finals.', 3500000),
            ('Electric Kettle', 'Boils water fast. Essential.', 1200000),
            ('Python Crash Course', 'Book. Good condition.', 800000),
            ('PlayStation 5', 'Includes 2 controllers.', 85000000),
            ('Gaming Monitor 144Hz', 'No dead pixels.', 40000000),
            ('Mechanical Keyboard', 'Blue switches, very loud.', 15000000),
            ('Wireless Mouse', 'Logitech MX Master 3.', 18000000),
            ('Noise Cancelling Headphones', 'Sony WH-1000XM4.', 30000000),
            ('Dumbbell Set', 'Adjustable up to 20kg.', 22000000),
            ('Yoga Mat', 'Thick and comfortable.', 350000),
            ('Bicycle', 'Mountain bike, 21 gears.', 60000000),
            ('Acoustic Guitar', 'Yamaha F310.', 45000000),
            ('Coffee Maker', 'Drip coffee machine.', 2500000),
            ('Microwave', '700W, clean inside.', 15000000),
            ('Backpack', 'North Face, waterproof.', 12000000)
        ]
        
        for title, desc, price in items:
            db.add(Listing(seller_id=user.id, title=title, description=desc, price_kobo=price))
        
        await db.commit()
        print('Seeded 20 listings successfully!')

asyncio.run(seed())
