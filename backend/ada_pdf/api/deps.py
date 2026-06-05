"""FastAPI dependency injectors."""
from __future__ import annotations

import hashlib
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ada_pdf.db.session import get_session
from ada_pdf.models.orm import ApiKey, KeyStatus


async def get_db(request: Request) -> AsyncSession:
    async for session in get_session():
        yield session


async def get_current_api_key(
    x_api_key: Annotated[Optional[str], Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header is required",
        )
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == KeyStatus.ACTIVE)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key",
        )
    return api_key
