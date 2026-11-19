from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import get_settings
from app.database import engine
from app.errors import install_error_handlers
from app.receipts import MemoryReceiptStorage
from app.routers import auth, categories, expenses, notifications, reports


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    app.state.redis = redis
    app.state.receipt_storage = MemoryReceiptStorage()
    try:
        await redis.ping()
        yield
    finally:
        await redis.aclose()
        await engine.dispose()


app = FastAPI(
    title="Expense Tracker API",
    version="1.0.0",
    lifespan=lifespan,
)
install_error_handlers(app)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(categories.router, prefix="/api/v1")
app.include_router(expenses.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
