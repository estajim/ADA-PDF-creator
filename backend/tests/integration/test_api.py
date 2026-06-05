"""Integration tests for the FastAPI application."""
from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from ada_pdf.api.app import create_app


@pytest.fixture
def app(settings):
    application = create_app()
    application.state.settings = settings
    return application


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health(app):
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_requires_api_key(app):
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/documents", files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")})
    assert resp.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_rejects_non_pdf(app):
    """Files that don't start with %PDF should be rejected."""
    fake_key = "test-api-key-abc"
    key_hash = __import__("hashlib").sha256(fake_key.encode()).hexdigest()

    with (
        patch("ada_pdf.api.deps.get_session") as mock_sess,
        patch("ada_pdf.api.routers.documents.process_document") as mock_task,
    ):
        # Mock DB returns a valid API key
        mock_api_key = MagicMock()
        mock_api_key.id = uuid.uuid4()
        mock_api_key.is_active.value = "active"

        db_mock = AsyncMock()
        db_mock.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=mock_api_key))
        mock_sess.return_value.__aiter__.return_value = [db_mock]

        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/documents",
                files={"file": ("test.txt", b"not a pdf file", "text/plain")},
                headers={"X-API-Key": fake_key},
            )
    assert resp.status_code == 400


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_status_404(app):
    """Unknown job ID should return 404."""
    fake_key = "test-api-key-abc"

    with patch("ada_pdf.api.deps.get_session") as mock_sess:
        mock_api_key = MagicMock()
        mock_api_key.id = uuid.uuid4()

        db_mock = AsyncMock()
        db_mock.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_api_key)
        )
        mock_sess.return_value.__aiter__.return_value = [db_mock]

        # Second DB call (get job) returns None
        db_mock.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_api_key)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]

        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.get(
                f"/documents/{uuid.uuid4()}/status",
                headers={"X-API-Key": fake_key},
            )
    assert resp.status_code == 404
