from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime, timezone

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.models import IncomeRecord, User

router = APIRouter(prefix="/income", tags=["income"])


class IncomeIn(BaseModel):
    source: str = "Salary"
    amount: Decimal
    frequency: str = "monthly"
    expected_date: datetime | None = None


@router.get("")
async def list_income(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(IncomeRecord).where(IncomeRecord.user_id == user.id))
    return res.scalars().all()


@router.post("", status_code=201)
async def create_income(payload: IncomeIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    inc = IncomeRecord(user_id=user.id, source=payload.source, amount=payload.amount,
                      frequency=payload.frequency, expected_date=payload.expected_date or datetime.now(timezone.utc))
    db.add(inc)
    await db.commit()
    await db.refresh(inc)
    return inc


@router.put("/{iid}")
async def update_income(iid: int, payload: IncomeIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(IncomeRecord).where(IncomeRecord.id == iid, IncomeRecord.user_id == user.id))
    inc = res.scalar_one_or_none()
    if not inc:
        return {"error": "not found"}
    inc.source = payload.source
    inc.amount = payload.amount
    inc.frequency = payload.frequency
    if payload.expected_date:
        inc.expected_date = payload.expected_date
    await db.commit()
    await db.refresh(inc)
    return inc
