from typing import TYPE_CHECKING, List, Optional, Text, Union

import google.generativeai as genai
from google.generativeai.types.content_types import ContentDict

from languru.action.base import ActionBase, ModelDeploy
from languru.llm.config import logger

if TYPE_CHECKING:
    from openai.types import Completion, CreateEmbeddingResponse
    from openai.types.chat import ChatCompletion, ChatCompletionMessageParam


class GoogleGenAiAction(ActionBase):
    model_deploys = (
        ModelDeploy("models/chat-bison-001", "models/chat-bison-001"),
        ModelDeploy("models/text-bison-001", "models/text-bison-001"),
        ModelDeploy("models/embedding-gecko-001", "models/embedding-gecko-001"),
        ModelDeploy("models/gemini-1.0-pro", "models/gemini-1.0-pro"),
        ModelDeploy("models/gemini-1.0-pro-001", "models/gemini-1.0-pro-001"),
        ModelDeploy("models/gemini-1.0-pro-latest", "models/gemini-1.0-pro-latest"),
        ModelDeploy(
            "models/gemini-1.0-pro-vision-latest", "models/gemini-1.0-pro-vision-latest"
        ),
        ModelDeploy("models/gemini-pro", "models/gemini-pro"),
        ModelDeploy("models/gemini-pro-vision", "models/gemini-pro-vision"),
        ModelDeploy("models/embedding-001", "models/embedding-001"),
        ModelDeploy("models/aqa", "models/aqa"),
        ModelDeploy("chat-bison-001", "models/chat-bison-001"),
        ModelDeploy("text-bison-001", "models/text-bison-001"),
        ModelDeploy("embedding-gecko-001", "models/embedding-gecko-001"),
        ModelDeploy("gemini-1.0-pro", "models/gemini-1.0-pro"),
        ModelDeploy("gemini-1.0-pro-001", "models/gemini-1.0-pro-001"),
        ModelDeploy("gemini-1.0-pro-latest", "models/gemini-1.0-pro-latest"),
        ModelDeploy(
            "gemini-1.0-pro-vision-latest", "models/gemini-1.0-pro-vision-latest"
        ),
        ModelDeploy("gemini-pro", "models/gemini-pro"),
        ModelDeploy("gemini-pro-vision", "models/gemini-pro-vision"),
        ModelDeploy("embedding-001", "models/embedding-001"),
        ModelDeploy("aqa", "models/aqa"),
    )

    def __init__(
        self,
        *args,
        api_key: Optional[Text] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        genai.configure(api_key=api_key)

    def name(self) -> Text:
        return "google_genai_action"

    def health(self) -> bool:
        try:
            genai.get_model("gemini-pro")
            return True
        except Exception as e:
            logger.error(f"Google GenAI health check failed: {e}")
            return False

    def chat(
        self, messages: List["ChatCompletionMessageParam"], *args, model: Text, **kwargs
    ) -> "ChatCompletion":
        if len(messages) == 0:
            raise ValueError("messages must not be empty")

        # pop out the last message
        genai_model = genai.GenerativeModel(model)
        contents: List[ContentDict] = [
            ContentDict(role=m["role"], parts=[m["content"]])
            for m in messages
            if "content" in m and m["content"]
        ]
        input_tokens = genai_model.count_tokens(contents)

        latest_content = contents.pop()
        chat_session = genai_model.start_chat(history=contents or None)
        response = chat_session.send_message(latest_content)
        response.parts

    def text_completion(
        self, prompt: Text, *args, model: Text, **kwargs
    ) -> "Completion":
        raise NotImplementedError

    def embeddings(
        self,
        input: Union[Text, List[Union[Text, List[Text]]]],
        *args,
        model: Text,
        **kwargs,
    ) -> "CreateEmbeddingResponse":
        raise NotImplementedError
