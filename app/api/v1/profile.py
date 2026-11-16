from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime, timezone

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.models import BankAccount, IncomeRecord, SavingsRule, User

router = APIRouter(prefix="/profile", tags=["profile"])


class OnboardingIn(BaseModel):
    starting_balance: Decimal
    monthly_income: Decimal
    protected_savings: Decimal = Decimal("0")
    account_name: str = "Primary Account"


@router.post("/onboarding")
async def onboarding(payload: OnboardingIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    acc = BankAccount(user_id=user.id, name=payload.account_name, account_type="bank",
                      currency="INR", balance=payload.starting_balance, balance_source="manual")
    db.add(acc)
    inc = IncomeRecord(user_id=user.id, source="Salary", amount=payload.monthly_income, frequency="monthly",
                       expected_date=datetime.now(timezone.utc))
    db.add(inc)
    rule = SavingsRule(user_id=user.id, protected_monthly=payload.protected_savings)
    db.add(rule)
    await db.commit()
    return {"ok": True}


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    from sqlalchemy import func
    r1 = await db.execute(select(func.count()).select_from(BankAccount).where(BankAccount.user_id == user.id))
    r2 = await db.execute(select(func.count()).select_from(IncomeRecord).where(IncomeRecord.user_id == user.id))
    has_account = (r1.scalar() or 0) > 0
    has_income = (r2.scalar() or 0) > 0
    return {"onboarded": has_account and has_income}


@router.get("/currency")
async def get_currency(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(BankAccount).where(BankAccount.user_id == user.id).limit(1))
    acc = res.scalar_one_or_none()
    return {"currency": acc.currency if acc else "INR"}


@router.put("/currency")
async def set_currency(currency: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(BankAccount).where(BankAccount.user_id == user.id))
    for a in res.scalars().all():
        a.currency = currency
    await db.commit()
    return {"currency": currency}
