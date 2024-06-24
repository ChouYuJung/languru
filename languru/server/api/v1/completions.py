from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse
from openai import OpenAI
from openai.types.completion import Completion
from pyassorted.asyncio.executor import run_func, run_generator

from languru.server.config import ServerBaseSettings
from languru.server.deps.common import app_settings
from languru.server.deps.openai_clients import openai_clients
from languru.types.completions import CompletionRequest
from languru.utils.http import simple_sse_encode

router = APIRouter()


class TextCompletionHandler:

    async def handle_request(
        self,
        request: "Request",
        *args,
        completion_request: "CompletionRequest",
        openai_client: "OpenAI",
        settings: "ServerBaseSettings",
        **kwargs,
    ) -> Completion | StreamingResponse:
        # Stream
        if completion_request.stream is True:
            return await self.handle_stream(
                request=request,
                completion_request=completion_request,
                openai_client=openai_client,
                settings=settings,
                **kwargs,
            )
        # Normal
        else:
            return await self.handle_normal(
                request=request,
                completion_request=completion_request,
                openai_client=openai_client,
                settings=settings,
                **kwargs,
            )

    async def handle_normal(
        self,
        request: "Request",
        *args,
        completion_request: "CompletionRequest",
        openai_client: "OpenAI",
        settings: "ServerBaseSettings",
        **kwargs,
    ) -> Completion:
        return await run_func(
            openai_client.completions.create,
            completion_request.model_dump(exclude_none=True),
        )

    async def handle_stream(
        self,
        request: "Request",
        *args,
        completion_request: "CompletionRequest",
        openai_client: "OpenAI",
        settings: "ServerBaseSettings",
        **kwargs,
    ) -> StreamingResponse:
        completion_stream_params = completion_request.model_dump(exclude_none=True)
        completion_stream_params.pop("stream", None)
        return StreamingResponse(
            run_generator(
                simple_sse_encode(
                    await run_func(
                        openai_client.completions.create,
                        **completion_stream_params,
                        stream=True,
                    )  # type: ignore
                )
            ),
            media_type="application/stream+json",
        )


@router.post("/completions")
async def text_completions(
    request: Request,
    completion_request: CompletionRequest = Body(
        ...,
        openapi_examples={
            "Quick text completion": {
                "summary": "Quick text completion",
                "description": "Text completion request",
                "value": {
                    "model": "gpt-3.5-turbo-instruct",
                    "prompt": "Say this is a test",
                    "max_tokens": 7,
                    "temperature": 0,
                },
            },
        },
    ),
    openai_client=Depends(openai_clients.depends_openai_client),
    settings: ServerBaseSettings = Depends(app_settings),
):  # openai.types.Completion
    return await TextCompletionHandler().handle_request(
        request=request,
        completion_request=completion_request,
        openai_client=openai_client,
        settings=settings,
    )
