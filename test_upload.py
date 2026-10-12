import asyncio
import httpx
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.core.security import create_access_token
from sqlalchemy import select

async def test():
    with open('test.jpg', 'wb') as f:
        f.write(b'dummy image data')
    
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User))
        user = res.scalars().first()
        token = create_access_token(user.id)
    
    async with httpx.AsyncClient() as client:
        headers = {'Authorization': f'Bearer {token}'}
        files = {'image': ('test.jpg', open('test.jpg', 'rb'), 'image/jpeg')}
        data = {'title': 'Test Upload', 'price_kobo': '500'}
        
        resp = await client.post('http://127.0.0.1:8000/api/v1/listings/', data=data, files=files, headers=headers)
        print('Upload Status:', resp.status_code)
        print('Upload Body:', resp.text)

asyncio.run(test())
