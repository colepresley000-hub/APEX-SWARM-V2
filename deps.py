"""
APEX SWARM — Shared FastAPI dependencies.

Lives in its own module so both main.py and the route modules under routes/
can import dependencies without creating a circular import (routers must never
import from main, since main imports the routers to register them).

Only the lightweight, self-contained dependencies live here. Heavier auth
dependencies that need license validation / admin keys stay in main.py for now.
"""
from fastapi import Header, HTTPException


def get_api_key(x_api_key: str = Header(None), authorization: str = Header(None)) -> str:
    """Extract API key from headers."""
    key = x_api_key or ""
    if not key and authorization:
        key = authorization.replace("Bearer ", "")
    if not key:
        raise HTTPException(status_code=401, detail="API key required. Pass X-Api-Key header.")
    return key
