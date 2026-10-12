import httpx; print(httpx.post('http://127.0.0.1:8000/api/v1/auth/register', json={'email':'tadeogidan@gmail.com','password':'password','profile':{'burner_handle':'prettyangel'}}).json())
