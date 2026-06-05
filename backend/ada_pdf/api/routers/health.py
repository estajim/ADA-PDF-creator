from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ada_pdf.api.schemas import HealthResponse, ReadyzResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def liveness():
    return HealthResponse()


@router.get("/readyz", response_model=ReadyzResponse)
async def readiness(request: Request):
    db_ok = False
    redis_ok = False

    try:
        from sqlalchemy import text
        from ada_pdf.db.session import get_session
        async for session in get_session():
            await session.execute(text("SELECT 1"))
            db_ok = True
            break
    except Exception:
        pass

    try:
        import redis.asyncio as aioredis
        settings = request.app.state.settings
        r = aioredis.from_url(settings.REDIS_URL)
        await r.ping()
        await r.aclose()
        redis_ok = True
    except Exception:
        pass

    status_str = "ok" if (db_ok and redis_ok) else "degraded"
    response = ReadyzResponse(status=status_str, db=db_ok, redis=redis_ok)
    code = 200 if status_str == "ok" else 503
    return JSONResponse(content=response.model_dump(), status_code=code)
