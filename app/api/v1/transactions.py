from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.redis import cache_delete
from app.models.models import Transaction, BankAccount, Category, User
from app.schemas.txn import TransactionIn, TransactionOut

router = APIRouter(prefix="/transactions", tags=["transactions"])


async def _category(db, user_id, name, kind):
    if not name:
        return None
    res = await db.execute(select(Category).where(Category.user_id == user_id, Category.name == name))
    c = res.scalar_one_or_none()
    if not c:
        c = Category(user_id=user_id, name=name, kind=kind)
        db.add(c)
        await db.flush()
    return c


@router.get("", response_model=list[TransactionOut])
async def list_txns(days: int | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    import datetime as _dt
    q = select(Transaction).where(Transaction.user_id == user.id)
    if days:
        q = q.where(Transaction.txn_date >= datetime.now(timezone.utc) - _dt.timedelta(days=days))
    q = q.order_by(Transaction.txn_date.desc()).limit(500)
    res = await db.execute(q)
    txns = res.scalars().all()
    cat_ids = {t.category_id for t in txns if t.category_id is not None}
    names = {}
    if cat_ids:
        cres = await db.execute(select(Category).where(Category.id.in_(cat_ids)))
        names = {c.id: c.name for c in cres.scalars().all()}
    return [
        TransactionOut(
            id=t.id,
            amount=float(t.amount),
            txn_type=t.txn_type,
            description=t.description,
            category_name=names.get(t.category_id),
            txn_date=t.txn_date,
            status=t.status,
        )
        for t in txns
    ]


@router.post("", response_model=TransactionOut, status_code=201)
async def create_txn(payload: TransactionIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    cat = await _category(db, user.id, payload.category, "expense" if payload.txn_type == "expense" else "income")
    txn = Transaction(
        user_id=user.id,
        account_id=payload.account_id,
        category_id=cat.id if cat else None,
        amount=payload.amount,
        txn_type=payload.txn_type,
        description=payload.description,
        txn_date=payload.txn_date or datetime.now(timezone.utc),
        status=payload.status,
    )
    db.add(txn)
    if payload.account_id and payload.status == "confirmed":
        res = await db.execute(select(BankAccount).where(BankAccount.id == payload.account_id, BankAccount.user_id == user.id))
        acc = res.scalar_one_or_none()
        if acc:
            if payload.txn_type == "expense":
                acc.balance = (acc.balance or Decimal("0")) - payload.amount
            elif payload.txn_type == "income":
                acc.balance = (acc.balance or Decimal("0")) + payload.amount
            acc.balance_updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(txn)
    cache_delete(f"dashboard:{user.id}")
    return TransactionOut(
        id=txn.id,
        amount=float(txn.amount),
        txn_type=txn.txn_type,
        description=txn.description,
        category_name=cat.name if cat else None,
        txn_date=txn.txn_date,
        status=txn.status,
    )


@router.put("/{txn_id}", response_model=TransactionOut)
async def update_txn(txn_id: int, payload: TransactionIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(Transaction).where(Transaction.id == txn_id, Transaction.user_id == user.id))
    txn = res.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="not found")
    cat = await _category(db, user.id, payload.category, "expense" if payload.txn_type == "expense" else "income")
    # reverse old balance effect? simple: adjust account balance difference
    txn.amount = payload.amount
    txn.txn_type = payload.txn_type
    txn.description = payload.description
    txn.category_id = cat.id if cat else txn.category_id
    txn.txn_date = payload.txn_date or txn.txn_date
    txn.status = payload.status
    await db.commit()
    await db.refresh(txn)
    cat_name = cat.name if cat else None
    if not cat_name and txn.category_id:
        cres = await db.execute(select(Category).where(Category.id == txn.category_id))
        old_cat = cres.scalar_one_or_none()
        cat_name = old_cat.name if old_cat else None
    cache_delete(f"dashboard:{user.id}")
    return TransactionOut(
        id=txn.id,
        amount=float(txn.amount),
        txn_type=txn.txn_type,
        description=txn.description,
        category_name=cat_name,
        txn_date=txn.txn_date,
        status=txn.status,
    )

@router.delete("/{txn_id}", status_code=204)
async def delete_txn(txn_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(Transaction).where(Transaction.id == txn_id, Transaction.user_id == user.id))
    txn = res.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=404, detail="not found")
    await db.delete(txn)
    await db.commit()
    cache_delete(f"dashboard:{user.id}")
