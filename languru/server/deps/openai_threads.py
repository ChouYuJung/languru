import asyncio
from logging import Logger
from typing import List, Optional, Text, Tuple

from fastapi import Body, Depends
from fastapi import Path as QueryPath
from fastapi import Request
from fastapi.exceptions import HTTPException
from openai import OpenAI
from openai.types.beta.assistant import Assistant
from openai.types.beta.threads.message import Message as ThreadsMessage
from openai.types.beta.threads.run import Run as ThreadsRun
from pyassorted.asyncio.executor import run_func

from languru.config import logger as languru_logger
from languru.exceptions import NotFound
from languru.resources.sql.openai.backend import OpenaiBackend
from languru.server.deps.openai_backend import depends_openai_backend
from languru.server.deps.openai_clients import openai_client_from_model, openai_clients
from languru.server.utils.common import get_value_from_app
from languru.types.openai_threads import ThreadsRunCreate
from languru.types.organizations import OrganizationType
from languru.utils.common import display_object


async def _retrieve_assistant(
    assistant: Text, *, openai_backend: OpenaiBackend
) -> Assistant:
    """Retrieve an assistant from the OpenAI backend."""

    try:
        assistant_retrieved = await run_func(
            openai_backend.assistants.retrieve, assistant_id=assistant
        )
    except NotFound:
        raise HTTPException(status_code=404, detail="Assistant not found.")
    return assistant_retrieved


async def _list_messages(
    thread_id: Text, *, openai_backend: OpenaiBackend
) -> List[ThreadsMessage]:
    """List messages in a thread from the OpenAI backend."""

    messages = await run_func(
        openai_backend.threads.messages.list,
        thread_id=thread_id,
    )
    return messages


async def depends_thread_id_run_messages_assistant_openai_client_backend(
    request: "Request",
    org_type: Optional[OrganizationType] = Depends(openai_clients.depends_org_type),
    thread_id: Text = QueryPath(
        ...,
        description="The ID of the thread to create a run in.",
    ),
    run_create_request: ThreadsRunCreate = Body(
        ...,
        description="The parameters for creating a run.",
    ),
    openai_backend: OpenaiBackend = Depends(depends_openai_backend),
) -> Tuple[Text, ThreadsRun, List[ThreadsMessage], Assistant, OpenAI, OpenaiBackend]:
    """Returns the thread ID, the OpenAI threads run, the OpenAI client, and the backend.

    Note
    ----
    * The assistant is retrieved from the OpenAI backend.
    * The messages are listed from the OpenAI backend.
    * The additional instructions are append after the assistant instructions in the Run progressing lifecycle.
    * The additional messages are appended to the thread messages in the Run progressing lifecycle.
    * The assistant model is used if not specified, and would be validated for OpenAI client.
    """  # noqa: E501

    logger = get_value_from_app(
        request.app, key="logger", value_typing=Logger, default=languru_logger
    )

    # Get the assistant and threads messages
    assistant, messages = await asyncio.gather(
        _retrieve_assistant(
            run_create_request.assistant_id, openai_backend=openai_backend
        ),
        _list_messages(thread_id, openai_backend=openai_backend),
    )

    # Retrieve the model if not specified
    if run_create_request.model is None:
        logger.debug(f"No model specified. Using assistant '{assistant.id}' model.")
        run_create_request.model = assistant.model

    # Retrieve the OpenAI client and the model name without organization type
    openai_client, org_type, run_create_request.model = openai_client_from_model(
        run_create_request.model, org_type=org_type
    )

    # Append additional messages
    if run_create_request.additional_messages:
        for m in run_create_request.additional_messages:
            messages.append(
                m.to_openai_message(thread_id=thread_id, status="completed")
            )

    # Create the OpenAI threads run
    run = run_create_request.to_openai_run(
        thread_id=thread_id,
        status="queued",
        default_instructions=assistant.instructions or "",
        default_temperature=assistant.temperature or 0.5,
        enable_additional_instructions=True,
    )

    logger.debug(
        "Depends OpenAI client threads run create request: "
        + f"organization type: '{org_type}', "
        + f"openAI client: '{display_object(openai_client)}', "
        + f"model: '{run_create_request.model}'"
    )
    return (thread_id, run, messages, assistant, openai_client, openai_backend)
