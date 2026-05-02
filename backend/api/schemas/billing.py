from typing import Optional

from pydantic import BaseModel


class BillingPriceRuleRequest(BaseModel):
    rule_name: str
    category: str
    stage: str = ""
    provider: str = ""
    model_name: str = ""
    resolution: str = ""
    billing_mode: str
    unit_price_rmb: float
    is_active: bool = True
    priority: int = 0
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
