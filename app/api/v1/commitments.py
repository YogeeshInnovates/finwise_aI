from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from datetime import datetime
from decimal import Decimal

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.models import FixedCommitment, User

router = APIRouter(prefix="/commitments", tags=["commitments"])


class CommitmentIn(BaseModel):
    name: str
    amount: Decimal
    due_date: datetime
    recurrence: str = "monthly"


@router.get("")
async def list_commitments(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(FixedCommitment).where(FixedCommitment.user_id == user.id).order_by(FixedCommitment.due_date))
    return res.scalars().all()


@router.post("", status_code=201)
async def create_commitment(payload: CommitmentIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    c = FixedCommitment(user_id=user.id, name=payload.name, amount=payload.amount, due_date=payload.due_date, recurrence=payload.recurrence)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


@router.post("/{cid}/pay")
async def pay_commitment(cid: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(FixedCommitment).where(FixedCommitment.id == cid, FixedCommitment.user_id == user.id))
    c = res.scalar_one_or_none()
    if not c:
        return {"error": "not found"}
    c.status = "paid"
    await db.commit()
    return {"status": "paid"}
