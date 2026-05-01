from pydantic import BaseModel


class UpdateModelConfigRequest(BaseModel):
    model_id: str = ""
