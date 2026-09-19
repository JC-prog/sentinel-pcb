from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GoldenImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    board_id: str
    component_ref: str
    package: str
    feature: str
    notes: str | None
    created_at: datetime
