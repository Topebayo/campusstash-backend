import asyncio
import uuid
from app.db.session import AsyncSessionLocal
from app.models.campus import Campus
from sqlalchemy import select, update

ibadan_schools = [
    {
        'id': uuid.UUID('7b38d39f-2f78-4389-980e-3fa5e9b9281a'),
        'name': 'The Polytechnic, Ibadan',
        'acronym': 'Poly Ibadan',
        'state': 'Ibadan',
        'popular_hostels': ['North Campus', 'South Campus', 'Apete', 'Middle Gate', 'Ologuneru']
    },
    {
        'id': uuid.UUID('e4b52cb1-5367-4228-b210-917711468d60'),
        'name': 'KolaDaisi University',
        'acronym': 'KDU',
        'state': 'Ibadan',
        'popular_hostels': ['KM 18 Ibadan-Oyo Expressway', 'Kings Hall', 'Queens Hall']
    },
    {
        'id': uuid.UUID('3f8b059e-e317-4581-996b-0726b219fa51'),
        'name': 'First Technical University',
        'acronym': 'Tech-U',
        'state': 'Ibadan',
        'popular_hostels': ['University Hostel', 'Lagos-Ibadan Expressway', 'Alomaja']
    },
    {
        'id': uuid.UUID('c98fa326-809f-4318-ba90-2771fef45823'),
        'name': 'Dominican University',
        'acronym': 'DU',
        'state': 'Ibadan',
        'popular_hostels': ['Dominican Student Hostel', 'Samonda', 'Poly Road']
    },
    {
        'id': uuid.UUID('5d2780ca-ebf0-42f5-b982-f3f009405d15'),
        'name': 'Precious Cornerstone University',
        'acronym': 'PCU',
        'state': 'Ibadan',
        'popular_hostels': ['Grace Hall', 'Peace Hall', 'Olaogun']
    },
    {
        'id': uuid.UUID('8e7421cb-4ef9-4971-8bc4-c6899451de70'),
        'name': 'Federal College of Animal Health and Production Technology',
        'acronym': 'FCAH&PT',
        'state': 'Ibadan',
        'popular_hostels': ['Moor Plantation', 'Apata', 'College Hostel']
    },
    {
        'id': uuid.UUID('a6d39478-f7b9-4089-9132-723eb8f27802'),
        'name': 'Federal College of Forestry',
        'acronym': 'FCF',
        'state': 'Ibadan',
        'popular_hostels': ['Jericho Campus', 'Forestry Hall', 'Dugbe']
    },
    {
        'id': uuid.UUID('d5c78912-320e-4ab8-8b54-914068f23471'),
        'name': 'Federal School of Statistics',
        'acronym': 'FSS',
        'state': 'Ibadan',
        'popular_hostels': ['Sango Campus', 'UI Road']
    }
]

async def sync():
    async with AsyncSessionLocal() as db:
        # 1. Update UI and LCU state to 'Ibadan'
        await db.execute(update(Campus).where(Campus.acronym.in_(['LCU', 'UI'])).values(state='Ibadan'))
        print("Updated LCU and UI state to 'Ibadan'")
        
        # 2. Add other schools in Ibadan if not existing
        for sch in ibadan_schools:
            res = await db.execute(select(Campus).where(Campus.name == sch['name']))
            existing = res.scalar_one_or_none()
            if not existing:
                c = Campus(
                    id=sch['id'],
                    name=sch['name'],
                    acronym=sch['acronym'],
                    state=sch['state'],
                    popular_hostels=sch['popular_hostels']
                )
                db.add(c)
                print(f"Added: {sch['name']} ({sch['acronym']})")
            else:
                await db.execute(update(Campus).where(Campus.name == sch['name']).values(state='Ibadan', acronym=sch['acronym']))
                print(f"Updated: {sch['name']} ({sch['acronym']})")
        
        await db.commit()
        print('Database commit successful!')

        # Verify all campuses
        all_res = await db.execute(select(Campus).order_by(Campus.state.asc(), Campus.name.asc()))
        all_campuses = all_res.scalars().all()
        print(f"\nTotal campuses in database: {len(all_campuses)}")
        for c in all_campuses:
            print(f" - {c.name} ({c.acronym}) [{c.state}] ID: {c.id}")

if __name__ == '__main__':
    asyncio.run(sync())
