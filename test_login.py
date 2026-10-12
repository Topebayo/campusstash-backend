import asyncio
import httpx
async def test():
    async with httpx.AsyncClient() as client:
        response = await client.post('http://127.0.0.1:8000/api/v1/auth/login/access-token', data={'username':'tolatrewr@gmail.com','password':'password'})
        print(response.status_code)
        print(response.json())
asyncio.run(test())
