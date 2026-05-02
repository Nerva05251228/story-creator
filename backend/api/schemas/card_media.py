from typing import List, Optional

from pydantic import BaseModel


class ImageGenerationRequest(BaseModel):
    provider: Optional[str] = None
    model: str
    size: str = "1:1"
    resolution: Optional[str] = None
    n: int = 1
    reference_image_ids: Optional[List[int]] = []
    generation_mode: str = "default"
