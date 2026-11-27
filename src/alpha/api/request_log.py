"""One structured log line per request (ADR-011): operation ID, method, path,
status, duration. Never bodies, query strings, file names, or coordinates."""

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

logger = logging.getLogger("alpha.api")


def install_request_log(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        operation = uuid.uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        logger.info(
            json.dumps(
                {
                    "event": "request",
                    "operation_id": operation,
                    "method": request.method,
                    "path": request.url.path,  # path only: IDs, no query string
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                }
            )
        )
        response.headers["X-Operation-Id"] = operation
        return response
