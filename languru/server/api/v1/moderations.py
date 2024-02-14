import math
import random
import time

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from openai.types import ModerationCreateResponse
from pyassorted.asyncio.executor import run_func
from yarl import URL

from languru.resources.model.discovery import ModelDiscovery
from languru.server.config import settings
from languru.types.moderations import ModerationRequest

router = APIRouter()


@router.post("/moderations")
async def request_moderations(
    request: Request,
    moderation_request: ModerationRequest = Body(
        ...,
        example={"input": "I want to kill them."},
    ),
) -> ModerationCreateResponse:
    if getattr(request.app.state, "model_discovery", None) is None:
        raise ValueError("Model discovery is not initialized")
    model_discovery: "ModelDiscovery" = request.app.state.model_discovery
    models = await run_func(
        model_discovery.list,
        id=moderation_request.model,
        created_from=math.floor(time.time() - settings.MODEL_REGISTER_PERIOD),
    )
    if len(models) == 0:
        raise HTTPException(
            status_code=404, detail=f"Model '{moderation_request.model}' not found"
        )

    model = random.choice(models)
    url = URL(model.owned_by).with_path("/moderations")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            str(url), json=moderation_request.model_dump(exclude_none=True)
        )
        response.raise_for_status()
        return ModerationCreateResponse(**response.json())