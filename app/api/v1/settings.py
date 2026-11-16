from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.redis import cache_delete
from app.models.models import IncomeRecord, SavingsRule, User

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsIn(BaseModel):
    fixed_monthly_income: Decimal = Decimal("0")
    fixed_savings_percent: Decimal = Decimal("0")
    extra_savings_percent: Decimal = Decimal("0")
    fixed_monthly_expense: Decimal = Decimal("0")


async def _monthly_income(db: AsyncSession, user_id: int):
    res = await db.execute(
        select(IncomeRecord)
        .where(IncomeRecord.user_id == user_id, IncomeRecord.frequency == "monthly")
        .order_by(IncomeRecord.id)
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _rule(db: AsyncSession, user_id: int):
    res = await db.execute(select(SavingsRule).where(SavingsRule.user_id == user_id))
    return res.scalar_one_or_none()


async def _payload(db: AsyncSession, user: User) -> dict:
    inc = await _monthly_income(db, user.id)
    rule = await _rule(db, user.id)
    return {
        "fixed_monthly_income": float(inc.amount if inc else 0),
        "fixed_savings_percent": float(rule.savings_percent if rule else 0),
        "extra_savings_percent": float(rule.side_savings_percent if rule else 10),
        "fixed_monthly_expense": float(rule.fixed_monthly_expense if rule else 0),
    }


@router.get("")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _payload(db, user)


@router.put("")
async def update_settings(
    payload: SettingsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    inc = await _monthly_income(db, user.id)
    if inc:
        inc.amount = payload.fixed_monthly_income
        inc.frequency = "monthly"
    else:
        db.add(
            IncomeRecord(
                user_id=user.id,
                source="Salary",
                amount=payload.fixed_monthly_income,
                frequency="monthly",
                expected_date=datetime.now(timezone.utc),
            )
        )

    rule = await _rule(db, user.id)
    if not rule:
        rule = SavingsRule(user_id=user.id)
        db.add(rule)

    rule.savings_percent = payload.fixed_savings_percent
    rule.side_savings_percent = payload.extra_savings_percent
    rule.fixed_monthly_expense = payload.fixed_monthly_expense

    await db.commit()
    cache_delete(f"dashboard:{user.id}")
    return await _payload(db, user)
