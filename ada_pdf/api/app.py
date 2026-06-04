"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ada_pdf.api.middleware import RateLimitMiddleware, RequestIDMiddleware
from ada_pdf.api.routers import documents, health, setup as setup_router
from ada_pdf.config import get_settings
from ada_pdf.db.session import close_db, init_db
from ada_pdf.setup.installer import is_ml_ready
from ada_pdf.utils.logging import configure_logging

_STATIC_DIR = Path(__file__).parent.parent / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(settings)
        app.state.settings = settings
        if settings.SIMPLE_MODE:
            await _bootstrap_simple_mode(settings)
        yield
        await close_db()

    app = FastAPI(
        title="ADA PDF Accessibility Converter",
        description=(
            "Converts any PDF (digital, scanned, or mixed) into a fully "
            "ADA-compliant, PDF/UA-1 accessible PDF."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # Middleware (order matters — outermost applied last)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.RATE_LIMIT_PER_MINUTE)
    cors_origins = (
        ["*"] if settings.CORS_ALLOWED_ORIGINS.strip() == "*"
        else [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(setup_router.router)
    app.include_router(documents.router)

    # Serve the SPA — static assets first, then root catch-all
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_ui():
        # In SIMPLE_MODE (desktop app): redirect to setup screen until ML is ready
        if settings.SIMPLE_MODE and not is_ml_ready():
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/setup")
        return FileResponse(str(_STATIC_DIR / "index.html"))

    return app


async def _bootstrap_simple_mode(settings) -> None:
    """Create tables and seed the hardcoded API key on first run (SIMPLE_MODE only)."""
    import hashlib
    from sqlalchemy import text
    from ada_pdf.db.session import _engine, _session_factory
    from ada_pdf.models.orm import Base, ApiKey, KeyStatus

    # Create all tables (idempotent)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed the fixed dev key "test-key-local" if not already present
    _SIMPLE_KEY = "test-key-local"
    key_hash = hashlib.sha256(_SIMPLE_KEY.encode()).hexdigest()
    async with _session_factory() as session:
        from sqlalchemy import select
        existing = (await session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        )).scalar_one_or_none()
        if existing is None:
            import uuid
            session.add(ApiKey(
                id=uuid.uuid4(),
                key_hash=key_hash,
                name="simple-mode-key",
                is_active=KeyStatus.ACTIVE,
                rate_limit_per_minute=600,
            ))
            await session.commit()


app = create_app()
