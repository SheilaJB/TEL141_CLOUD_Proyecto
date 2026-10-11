"""Read-only authenticated Compute Manager catalog API."""

import hmac
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException

import config
import db
import repository


def require_internal_token(
    token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    expected = config.INTERNAL_SERVICE_TOKEN
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid internal service token")


Auth = Annotated[None, Depends(require_internal_token)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.open_pool()
    try:
        yield
    finally:
        db.close_pool()


app = FastAPI(title="Compute Manager", lifespan=lifespan)


@app.get("/health")
def health(_: Auth) -> dict[str, str]:
    return {"status": "ok", "service": "Compute Manager"}


@app.get("/images")
def list_images(_: Auth) -> list[dict]:
    with db.tx() as conn:
        return repository.list_images(conn)


@app.get("/images/{image_id}")
def get_image(image_id: int, _: Auth) -> dict:
    with db.tx() as conn:
        image = repository.get_image(conn, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return image
