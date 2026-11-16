from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from decimal import Decimal

from app.api.deps import get_current_user
from fastapi import HTTPException
from app.core.database import get_db
from app.core.redis import cache_delete
from app.models.models import BankAccount, User

router = APIRouter(prefix="/accounts", tags=["accounts"])


class AccountIn(BaseModel):
    name: str
    account_type: str = "bank"
    currency: str = "INR"
    balance: Decimal = Decimal("0")


@router.get("")
async def list_accounts(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(BankAccount).where(BankAccount.user_id == user.id))
    return res.scalars().all()


@router.post("", status_code=201)
async def create_account(payload: AccountIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    acc = BankAccount(user_id=user.id, name=payload.name, account_type=payload.account_type,
                      currency=payload.currency, balance=payload.balance)
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    cache_delete(f"dashboard:{user.id}")
    return acc


class AccountUpdate(BaseModel):
    name: str | None = None
    account_type: str | None = None
    currency: str | None = None
    balance: Decimal | None = None

@router.put("/{aid}")
async def update_account(aid: int, payload: AccountUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(BankAccount).where(BankAccount.id == aid, BankAccount.user_id == user.id))
    acc = res.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="not found")
    if payload.name is not None: acc.name = payload.name
    if payload.account_type is not None: acc.account_type = payload.account_type
    if payload.currency is not None: acc.currency = payload.currency
    if payload.balance is not None: acc.balance = payload.balance
    await db.commit()
    await db.refresh(acc)
    cache_delete(f"dashboard:{user.id}")
    return acc
