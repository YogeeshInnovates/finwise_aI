from pydantic import BaseModel
from datetime import datetime
from decimal import Decimal


class TransactionIn(BaseModel):
    amount: Decimal
    txn_type: str = "expense"
    description: str | None = None
    category: str | None = None
    account_id: int | None = None
    txn_date: datetime | None = None
    status: str = "confirmed"


class TransactionOut(BaseModel):
    id: int
    amount: float
    txn_type: str
    description: str | None
    category_name: str | None = None
    txn_date: datetime
    status: str

    model_config = {"from_attributes": True}
