from typing import Literal

from pydantic import BaseModel, SecretStr

LlmProvider = Literal["ollama", "openai"]


class ChatStreamRequest(BaseModel):
    conversation_id: str
    message: str
    image_ids: list[str] = []
    provider: LlmProvider = "ollama"
    # Bring-your-own-key: never persisted server-side, only used for this request. SecretStr
    # keeps it out of any accidental repr/log of the request object.
    openai_api_key: SecretStr | None = None
