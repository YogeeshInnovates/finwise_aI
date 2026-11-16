from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from decimal import Decimal

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.models import SavingsRule, User

router = APIRouter(prefix="/savings", tags=["savings"])


class SavingsIn(BaseModel):
    protected_monthly: Decimal = Decimal("0")
    savings_percent: Decimal = Decimal("0")
    side_savings_percent: Decimal = Decimal("10")
    emergency_target: Decimal = Decimal("0")
    emergency_current: Decimal = Decimal("0")


@router.get("")
async def get_savings(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(SavingsRule).where(SavingsRule.user_id == user.id))
    r = res.scalar_one_or_none()
    if not r:
        r = SavingsRule(user_id=user.id)
        db.add(r)
        await db.commit()
        await db.refresh(r)
    return r


@router.put("")
async def update_savings(payload: SavingsIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(SavingsRule).where(SavingsRule.user_id == user.id))
    r = res.scalar_one_or_none()
    if not r:
        r = SavingsRule(user_id=user.id)
        db.add(r)
    r.protected_monthly = payload.protected_monthly
    r.savings_percent = payload.savings_percent
    r.side_savings_percent = payload.side_savings_percent
    r.emergency_target = payload.emergency_target
    r.emergency_current = payload.emergency_current
    await db.commit()
    await db.refresh(r)
    return r
