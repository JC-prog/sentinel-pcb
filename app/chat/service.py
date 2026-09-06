from app.chat.providers.ollama import OllamaChatService
from app.chat.providers.openai import OpenAiChatService
from app.chat.schemas import LlmProvider
from app.core.chat import ChatService

__all__ = ["ChatService", "get_chat_service"]


def get_chat_service(provider: LlmProvider) -> ChatService:
    if provider == "openai":
        return OpenAiChatService()
    return OllamaChatService()
