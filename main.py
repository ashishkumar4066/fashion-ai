"""
Fashion AI — root entry point.

Run the server:
    python main.py

Environment variables (optional, defaults shown):
    HOST     0.0.0.0
    PORT     8000
    RELOAD   true   (set to false in production)
"""

import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from api.routers import model as model_router
from core.logging import configure_logging

HOST   = os.getenv("HOST", "0.0.0.0")
PORT   = int(os.getenv("PORT", "8000"))
RELOAD = os.getenv("RELOAD", "true").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    yield


app = FastAPI(
    title="Fashion AI API",
    description="AI Fashion Virtual Try-On — backend API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(model_router.router, prefix="/api/v1", tags=["Model Generation"])


@app.get("/health", tags=["Health"])
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════╗
║         Fashion AI Server                ║
╠══════════════════════════════════════════╣
║  Host    : {HOST:<30} ║
║  Port    : {PORT:<30} ║
║  Reload  : {str(RELOAD):<30} ║
╠══════════════════════════════════════════╣
║  API     : http://localhost:{PORT}/api/v1  ║
║  Docs    : http://localhost:{PORT}/docs    ║
║  Health  : http://localhost:{PORT}/health  ║
╚══════════════════════════════════════════╝
""")

    uvicorn.run("main:app", host=HOST, port=PORT, reload=RELOAD)
