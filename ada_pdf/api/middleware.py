"""Rate limiting and request ID middleware."""
from __future__ import annotations

import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed sliding-window rate limiter (requests per minute per API key).

    Falls back to in-process tracking if Redis is unavailable, so the app
    continues to function during Redis downtime — it just loses cross-worker
    rate-limit coordination.
    """

    def __init__(self, app, requests_per_minute: int = 10):
        super().__init__(app)
        self.rpm = requests_per_minute
        self._redis = None
        self._fallback: dict[str, list[float]] = {}

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis as _redis_lib
            from ada_pdf.config import get_settings
            settings = get_settings()
            self._redis = _redis_lib.from_url(settings.REDIS_URL, decode_responses=True,
                                               socket_connect_timeout=1,
                                               socket_timeout=1)
            self._redis.ping()
            return self._redis
        except Exception:
            self._redis = None
            return None

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        api_key = request.headers.get("x-api-key", "anonymous")
        now = time.time()
        window_start = now - 60

        allowed = True
        r = self._get_redis()

        if r is not None:
            try:
                pipe_key = f"ratelimit:{api_key}"
                pipe = r.pipeline()
                pipe.zremrangebyscore(pipe_key, "-inf", window_start)
                pipe.zadd(pipe_key, {str(now): now})
                pipe.zcard(pipe_key)
                pipe.expire(pipe_key, 120)
                results = pipe.execute()
                count = results[2]
                if count > self.rpm:
                    allowed = False
            except Exception:
                self._redis = None  # drop Redis on error, fall through to in-process
                r = None

        if r is None:
            # In-process fallback
            window = [t for t in self._fallback.get(api_key, []) if t > window_start]
            if len(window) >= self.rpm:
                allowed = False
            else:
                window.append(now)
                self._fallback[api_key] = window

        if not allowed:
            return Response(
                content='{"error":"rate_limit_exceeded","message":"Too many requests"}',
                status_code=429,
                headers={"Retry-After": "60", "Content-Type": "application/json"},
            )

        return await call_next(request)
