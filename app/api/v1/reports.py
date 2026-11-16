from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta
import csv, io

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.models import User, Transaction

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/summary")
async def summary(days: int = 30, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    start = datetime.now(timezone.utc) - timedelta(days=days)
    res = await db.execute(select(Transaction).where(Transaction.user_id == user.id, Transaction.txn_date >= start))
    txns = res.scalars().all()
    income = sum(float(t.amount) for t in txns if t.txn_type == "income")
    expense = sum(float(t.amount) for t in txns if t.txn_type == "expense")
    return {"days": days, "income": income, "expense": expense, "net": income - expense, "count": len(txns)}


@router.get("/export.csv")
async def export_csv(days: int = 30, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    start = datetime.now(timezone.utc) - timedelta(days=days)
    res = await db.execute(select(Transaction).where(Transaction.user_id == user.id, Transaction.txn_date >= start))
    txns = res.scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "date", "type", "amount", "description"])
    for t in txns:
        w.writerow([t.id, t.txn_date, t.txn_type, t.amount, t.description])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                            headers={"Content-Disposition": "attachment; filename=transactions.csv"})
