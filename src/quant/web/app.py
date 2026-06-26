"""FastAPI app factory for the results dashboard.

Pattern adapted (in shape only) from the MIT-licensed daily_stock_analysis
`api/app.py`: a `create_app()` factory wiring CORS + a versioned router + a
served single-page frontend, with FastAPI's `/docs` as a free interactive API.

Read-only: the routes never place an order. CORS is open because this is meant
to run locally (`quant web`, bound to 127.0.0.1 by default).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

_STATIC = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Quant Results Dashboard",
        version="0.1.0",
        description="Read-only view of backtest / portfolio / journal results. "
                    "No order routing — live trading stays in the CLI.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],          # local-only tool
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from quant.web.routes import router
    app.include_router(router, prefix="/api")

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> Response:
        page = _STATIC / "index.html"
        if page.is_file():
            return FileResponse(page)
        return HTMLResponse("<h1>dashboard page missing</h1>", status_code=500)

    return app


app = create_app()
