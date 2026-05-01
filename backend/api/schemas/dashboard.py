from typing import List, Optional

from pydantic import BaseModel


class DashboardBulkDeleteRequest(BaseModel):
    ids: List[int] = []
    status: Optional[str] = None
    task_type: Optional[str] = None
    creator_username: Optional[str] = None
    keyword: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    delete_all: bool = False
