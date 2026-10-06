"""Standalone read-only map API; does not initialize or migrate the application DB."""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routes.map import router

app = FastAPI(title="MOYAK Map API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "MAP_ALLOWED_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080"
    ).split(",") if origin.strip()],
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "map"}
