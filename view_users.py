import asyncio
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.session import AsyncSessionLocal
from app.models.user import User

async def display_users():
    print("\n" + "=" * 80)
    print("                CAMPUSSTASH - REGISTERED USERS DATABASE                ")
    print("=" * 80)
    async with AsyncSessionLocal() as db:
        stmt = select(User).options(selectinload(User.profile)).order_by(User.created_at.desc())
        result = await db.execute(stmt)
        users = result.scalars().all()

        if not users:
            print("No users registered yet.")
            print("=" * 80 + "\n")
            return

        print(f"{'EMAIL':<30} | {'PHONE':<15} | {'BURNER HANDLE':<20} | {'STATUS'}")
        print("-" * 80)
        for u in users:
            handle = f"@{u.profile.burner_handle}" if u.profile and u.profile.burner_handle else "none"
            phone = u.phone or "Not provided"
            status = "Active" if u.is_active else "Suspended"
            print(f"{u.email:<30} | {phone:<15} | {handle:<20} | {status}")
        
        print("=" * 80)
        print(f"Total Registered Users: {len(users)}\n")

if __name__ == "__main__":
    asyncio.run(display_users())
