from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
import json

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.redis import cache_get, cache_set
from app.models.models import User
from app.services.finance import user_balance, flexible_budget, category_breakdown

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    key = f"dashboard:{user.id}"
    cached = cache_get(key)
    if cached:
        return json.loads(cached)
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(month=start.month % 12 + 1, year=start.year + (1 if start.month == 12 else 0))
    flexible = await flexible_budget(db, user.id)
    data = {
        "balance": float(await user_balance(db, user.id)),
        "month_income": flexible["month_income"],
        "month_expense": flexible["month_expense"],
        "flexible": flexible,
        "categories": await category_breakdown(db, user.id, start, end),
    }
    cache_set(key, json.dumps(data), 120)
    return data
