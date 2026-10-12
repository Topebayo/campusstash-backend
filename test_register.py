import asyncio
import httpx
async def test():
    async with httpx.AsyncClient() as client:
        response = await client.post('http://127.0.0.1:8000/api/v1/auth/register', json={'email':'tadeogidan@gmail.com','password':'password','profile':{'burner_handle':'prettyangel'}})
        print(response.status_code)
        print(response.json())
asyncio.run(test())
