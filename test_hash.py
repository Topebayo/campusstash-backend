from app.core.security import get_password_hash, verify_password
print('Hashing...')
h = get_password_hash('password')
print('Hashed:', h)
print('Verifying...')
print('Valid:', verify_password('password', h))
