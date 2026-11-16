from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Transaction, BankAccount, SavingsRule, IncomeRecord, Category


async def user_balance(db: AsyncSession, user_id: int) -> Decimal:
    res = await db.execute(select(BankAccount).where(BankAccount.user_id == user_id))
    accs = res.scalars().all()
    return sum((a.balance for a in accs), Decimal("0"))


async def period_totals(db: AsyncSession, user_id: int, start: datetime, end: datetime):
    res = await db.execute(select(Transaction.txn_type, func.sum(Transaction.amount)).where(
        Transaction.user_id == user_id,
        Transaction.status == "confirmed",
        Transaction.txn_date >= start,
        Transaction.txn_date < end,
    ).group_by(Transaction.txn_type))
    income = Decimal("0")
    expense = Decimal("0")
    for t, amt in res.all():
        if t == "income":
            income += amt or Decimal("0")
        elif t == "expense":
            expense += amt or Decimal("0")
    # include income records
    incs = await db.execute(select(IncomeRecord).where(IncomeRecord.user_id == user_id))
    for i in incs.scalars().all():
        income += i.amount or Decimal("0")
    return income, expense


async def category_breakdown(db: AsyncSession, user_id: int, start: datetime, end: datetime):
    res = await db.execute(select(Category.name, func.sum(Transaction.amount)).select_from(Transaction).outerjoin(
        Category, Transaction.category_id == Category.id
    ).where(
        Transaction.user_id == user_id,
        Transaction.txn_type == "expense",
        Transaction.status == "confirmed",
        Transaction.txn_date >= start,
        Transaction.txn_date < end,
    ).group_by(Category.name))
    return [{"category": r[0] or "Uncategorized", "amount": float(r[1] or 0)} for r in res.all()]


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(dt: datetime) -> datetime:
    return dt.replace(month=dt.month % 12 + 1, year=dt.year + (1 if dt.month == 12 else 0))


async def flexible_budget(db: AsyncSession, user_id: int):
    res = await db.execute(select(SavingsRule).where(SavingsRule.user_id == user_id))
    rules = res.scalar_one_or_none()
    fixed_pct = Decimal(rules.savings_percent if rules else 0)
    side_pct = Decimal(rules.side_savings_percent if rules else 10)
    fixed_expense = Decimal(rules.fixed_monthly_expense if rules else 0)

    now = datetime.now(timezone.utc)
    current_start = _month_start(now)

    res = await db.execute(
        select(IncomeRecord)
        .where(IncomeRecord.user_id == user_id, IncomeRecord.frequency == "monthly")
        .order_by(IncomeRecord.created_at)
    )
    monthly_records = res.scalars().all()

    if monthly_records:
        first = min((i.created_at or now) for i in monthly_records)
        start = _month_start(first)
    else:
        start = current_start
    if start > current_start:
        start = current_start

    res = await db.execute(select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.status == "confirmed",
        Transaction.txn_date >= start,
        Transaction.txn_date < _next_month(current_start),
    ))
    buckets = {}
    for t in res.scalars().all():
        key = (t.txn_date.year, t.txn_date.month)
        b = buckets.setdefault(key, {"income": Decimal("0"), "expense": Decimal("0")})
        if t.txn_type == "income":
            b["income"] += t.amount or Decimal("0")
        elif t.txn_type == "expense":
            b["expense"] += t.amount or Decimal("0")

    savings_total = Decimal("0")
    savings_used_total = Decimal("0")
    carry = Decimal("0")
    prev_deficit = Decimal("0")
    rollover = Decimal("0")
    flexible = Decimal("0")
    current = {
        "month_income": Decimal("0"),
        "side_income": Decimal("0"),
        "month_expense": Decimal("0"),
        "monthly_savings": Decimal("0"),
        "side_savings": Decimal("0"),
        "savings_used": Decimal("0"),
    }

    month = start
    while month <= current_start:
        end = _next_month(month)
        active_income = sum(
            (i.amount or Decimal("0") for i in monthly_records if (i.created_at or now) < end),
            Decimal("0"),
        )
        b = buckets.get((month.year, month.month), {"income": Decimal("0"), "expense": Decimal("0")})
        side_income = b["income"]
        expense = b["expense"]
        monthly_savings = active_income * fixed_pct / Decimal("100")
        side_savings = side_income * side_pct / Decimal("100")
        month_flex = active_income + side_income - monthly_savings - side_savings - expense

        savings_total += monthly_savings + side_savings
        rollover_this_month = carry
        carry += month_flex

        deficit = max(Decimal("0"), -carry)
        erosion = max(Decimal("0"), deficit - prev_deficit)
        savings_used = min(savings_total, erosion)
        savings_total -= savings_used
        savings_used_total += savings_used
        prev_deficit = deficit

        if month == current_start:
            rollover = rollover_this_month
            flexible = carry
            current = {
                "month_income": active_income,
                "side_income": side_income,
                "month_expense": expense,
                "monthly_savings": monthly_savings,
                "side_savings": side_savings,
                "savings_used": savings_used,
            }
        month = end

    return {
        "month_income": float(current["month_income"]),
        "side_income": float(current["side_income"]),
        "month_expense": float(current["month_expense"]),
        "monthly_savings": float(current["monthly_savings"]),
        "side_savings": float(current["side_savings"]),
        "savings_this_month": float(current["monthly_savings"] + current["side_savings"]),
        "savings_used_this_month": float(current["savings_used"]),
        "savings_used_total": float(savings_used_total),
        "savings_total": float(savings_total),
        "fixed_monthly_expense": float(fixed_expense),
        "fixed_savings_percent": float(fixed_pct),
        "extra_savings_percent": float(side_pct),
        "rollover": float(rollover),
        "flexible_remaining": float(flexible),
        "protected_savings": float(savings_total),
        "committed_unpaid": 0.0,
        "goal_allocations": 0.0,
    }


async def affordability(db: AsyncSession, user_id: int, cost: Decimal):
    fb = await flexible_budget(db, user_id)
    avail = Decimal(str(fb["flexible_remaining"]))
    shortfall = cost - avail
    return {
        "cost": float(cost),
        "available": float(avail),
        "affordable": shortfall <= 0,
        "shortfall": float(shortfall) if shortfall > 0 else 0.0,
    }
