from pydantic import BaseModel


class ChatStreamRequest(BaseModel):
    conversation_id: str
    message: str
    image_ids: list[str] = []
