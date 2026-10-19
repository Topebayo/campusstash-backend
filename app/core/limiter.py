from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse

# Shared SlowAPI Limiter instance
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["120/minute"],
    headers_enabled=False,
    strategy="moving-window"
)

def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Custom handler for rate limit exceeded exceptions.
    Returns standard JSON response with 429 status code and retry header.
    """
    response = JSONResponse(
        status_code=429,
        content={
            "detail": f"Rate limit exceeded: {exc.detail}. Please slow down your requests.",
            "error": "rate_limit_exceeded"
        }
    )
    response.headers["Retry-After"] = str(getattr(exc, "retry_after", 60))
    return response
