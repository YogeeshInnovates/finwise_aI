import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from langchain_core.messages import SystemMessage, HumanMessage

from app.agents.llm import get_chat_model, get_extract_model
from app.core.database import AsyncSessionLocal
from app.models.models import (
    User, BankAccount, Transaction, Category, IncomeRecord, SavingsRule, Goal, FixedCommitment,
)
from app.services.finance import user_balance, flexible_budget
from sqlalchemy import select


class AgentState(TypedDict, total=False):
    user_id: int
    message: str
    currency: str
    onboarded: bool
    intent: str
    extracted: dict
    txns_created: list
    pending_kind: str
    pending_value: float
    pending_action: Optional[str]
    _trip: dict
    trip_params: dict
    trip_estimate: dict
    reply: str


def _llm_json(prompt: str) -> dict:
    model = get_extract_model()
    if model is None:
        return {}
    resp = model.invoke([SystemMessage(content="Return ONLY valid JSON. No extra text."), HumanMessage(content=prompt)])
    try:
        return json.loads(resp.content)
    except Exception:
        return {}


async def _get_user(db, user_id):
    res = await db.execute(select(User).where(User.id == user_id))
    return res.scalar_one_or_none()


async def onboarding_check(state: AgentState):
    async with AsyncSessionLocal() as db:
        user_id = state["user_id"]
        acc = (await db.execute(select(BankAccount).where(BankAccount.user_id == user_id))).scalars().first()
        incs = (await db.execute(select(IncomeRecord).where(IncomeRecord.user_id == user_id))).scalars().all()
        if not acc or not incs:
            return {"onboarded": False, "reply": "Welcome! Before I can help, please complete your initial setup (bank balance, monthly income, protected savings). [ACTION:ONBOARD]"}
        acc0 = acc
        return {"onboarded": True, "currency": acc0.currency or "INR"}


async def classify_intent(state: AgentState):
    msg = state["message"]
    low = msg.lower()
    # number extraction with k support
    m = re.search(r"(\d+(?:\.\d+)?)\s*k\b", low)
    if m:
        val = float(m.group(1)) * 1000
    else:
        nums = re.findall(r"\d+(?:\.\d+)?", msg)
        val = float(nums[0]) if nums else None

    settings_income = ("income" in low) and any(w in low for w in ["set", "update", "change", "monthly income", "add income"])
    if val is not None and (("bal" in low) or ("savings" in low) or ("protected" in low) or ("saving" in low) or settings_income):
        if "bal" in low:
            kind = "add_balance" if "add" in low else "balance"
        elif settings_income:
            kind = "income"
        else:
            kind = "savings"
        return {"intent": "balance_update", "pending_kind": kind, "pending_value": val}

    if any(w in low for w in ["trip", "travel", "vacation", "holiday", "plane to", "plan to", "where can i go", "where i can go"]):
        return {"intent": "trip_plan"}

    if any(w in low for w in ["balance", "how much", "how many"]):
        return {"intent": "balance"}

    income_words = ["got", "receive", "received", "earn", "earned", "income", "salary", "bonus",
                    "freelance", "profit", "cashback", "refund", "credited", "side income",
                    "extra income", "gift", "won", "prize"]
    expense_words = ["spent", "spend", "bought", "buy", "paid", "pay", "purchase",
                     "expense", "cost me", "bill", "rent", "recharge"]
    has_income = any(w in low for w in income_words)
    has_expense = any(w in low for w in expense_words)
    if has_income and not has_expense:
        return {"intent": "income"}
    if has_expense and not has_income:
        return {"intent": "expense"}

    ip = (f'Classify this user message intent. Message: "{msg}". '
          'The user may have typos. If they got/received/earned money (salary, bonus, profit, freelance, gift), intent is "income". '
          'If they spent/bought/paid, intent is "expense". '
          'If asking about balance, "balance". If planning a trip, "trip_plan". Otherwise "general". '
          'Return JSON {"intent":"expense"|"income"|"trip_plan"|"balance"|"general"}.')
    intent = _llm_json(ip).get("intent", "general")
    return {"intent": intent}


async def expense_extract(state: AgentState):
    from app.agents.expense_agent import extract as extract_txns
    currency = state.get("currency", "INR")
    extracted = extract_txns(state["message"], currency)
    return {"extracted": extracted}


async def income_extract(state: AgentState):
    from app.agents.expense_agent import extract as extract_txns
    currency = state.get("currency", "INR")
    extracted = extract_txns(state["message"], currency, direction="income")
    for t in extracted.get("transactions", []) or []:
        t["type"] = "income"
    return {"extracted": extracted}


async def expense_confirm(state: AgentState):
    extracted = state.get("extracted", {})
    txns = extracted.get("transactions") or []
    if extracted.get("needs_confirmation") and extracted.get("question"):
        # interrupt to ask user
        decision = interrupt({"type": "confirm_expense", "question": extracted["question"], "transactions": txns})
        if str(decision).lower() in ("yes", "y", "ok", "okay", "proceed", "confirm"):
            return {"pending_action": "record_expense"}
        return {"reply": "Cancelled. No transactions recorded.", "pending_action": None}
    return {"pending_action": "record_expense"}


async def expense_record(state: AgentState):
    extracted = state.get("extracted", {})
    txns = extracted.get("transactions") or []
    user_id = state["user_id"]
    currency = state.get("currency", "INR")
    created = []
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(BankAccount).where(BankAccount.user_id == user_id))
        acc = res.scalars().first()
        for t in txns:
            amt = t.get("amount")
            if amt is None:
                continue
            ttype = str(t.get("type") or "expense").lower()
            if ttype not in ("income", "expense"):
                ttype = "income" if ttype in ("credit", "received", "earning", "earnings") else "expense"
            cat_name = t.get("category")
            cat = None
            if cat_name:
                r = await db.execute(select(Category).where(Category.user_id == user_id, Category.name == cat_name))
                cat = r.scalar_one_or_none()
                if not cat:
                    cat = Category(user_id=user_id, name=cat_name, kind=ttype)
                    db.add(cat)
                    await db.flush()
            txn = Transaction(
                user_id=user_id,
                account_id=acc.id if acc else None,
                category_id=cat.id if cat else None,
                amount=amt,
                currency=t.get("currency", currency),
                txn_type=ttype,
                description=t.get("description"),
                status="confirmed",
            )
            db.add(txn)
            if acc:
                if ttype == "expense":
                    acc.balance = (acc.balance or Decimal("0")) - Decimal(str(amt))
                elif ttype == "income":
                    acc.balance = (acc.balance or Decimal("0")) + Decimal(str(amt))
            created.append(f"{ttype} {amt} {t.get('currency',currency)} - {t.get('description')}")
        await db.commit()
        from app.core.redis import cache_delete
        cache_delete(f"dashboard:{user_id}")
    async with AsyncSessionLocal() as db:
        bal = await user_balance(db, user_id)
        fb = await flexible_budget(db, user_id)
    summary = "Recorded: " + "; ".join(created) + f". Balance now {bal}. Month expense {fb['month_expense']}."
    return {"reply": summary, "pending_action": None}


async def balance_update_detect(state: AgentState):
    kind = state.get("pending_kind")
    val = state.get("pending_value")
    currency = state.get("currency", "INR")
    verb = "add to" if kind == "add_balance" else "update"
    return {"pending_action": "confirm_balance", "reply": f"I can {verb} your bank balance by {val} {currency}. Can I proceed? Reply 'yes' to confirm."}


async def balance_confirm(state: AgentState):
    decision = interrupt({"type": "confirm_balance", "question": state.get("reply")})
    if str(decision).lower() in ("yes", "y", "ok", "okay", "proceed", "confirm"):
        return {"pending_action": "apply_balance"}
    return {"reply": "Cancelled. No changes made.", "pending_action": None}


async def balance_apply(state: AgentState):
    user_id = state["user_id"]
    kind = state.get("pending_kind")
    val = Decimal(str(state.get("pending_value")))
    currency = state.get("currency", "INR")
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(BankAccount).where(BankAccount.user_id == user_id))
        acc = res.scalars().first()
        if kind in ("balance", "add_balance"):
            if acc:
                if kind == "add_balance":
                    acc.balance = (acc.balance or Decimal("0")) + val
                else:
                    acc.balance = val
                await db.commit()
                from app.core.redis import cache_delete
                cache_delete(f"dashboard:{user_id}")
                newbal = acc.balance
                return {"reply": f"Updated your bank balance. New balance is {newbal} {currency}. [ACTION:REFRESH]", "pending_action": None}
        if kind == "income":
            res = await db.execute(select(IncomeRecord).where(IncomeRecord.user_id == user_id))
            inc = res.scalars().first()
            if inc:
                inc.amount = val
            else:
                inc = IncomeRecord(user_id=user_id, source="Salary", amount=val, frequency="monthly")
                db.add(inc)
            await db.commit()
            from app.core.redis import cache_delete
            cache_delete(f"dashboard:{user_id}")
            return {"reply": f"Updated your monthly income to {val} {currency}. [ACTION:REFRESH]", "pending_action": None}
        if kind == "savings":
            res = await db.execute(select(SavingsRule).where(SavingsRule.user_id == user_id))
            r = res.scalar_one_or_none()
            if not r:
                r = SavingsRule(user_id=user_id)
                db.add(r)
            r.protected_monthly = val
            await db.commit()
            from app.core.redis import cache_delete
            cache_delete(f"dashboard:{user_id}")
            return {"reply": f"Updated protected savings to {val} {currency}. [ACTION:REFRESH]", "pending_action": None}
    return {"reply": "Nothing to update.", "pending_action": None}


async def trip_missing_info(state: AgentState):
    msg = state["message"]
    params = _llm_json(f'Extract trip details from: "{msg}". Return JSON {{"destination":str or null,"days":number or null,"budget":number or null,"month":str or null,"transport":str or null}}.')
    dest = params.get("destination")
    days = params.get("days")
    if not dest:
        return {"reply": "I'd love to help! Tell me where you want to go, for how many days, and your budget (e.g. \"Trip to Mysore for 2 days by bike with 6000 budget\").", "trip_params": params}
    if not days:
        days = 2
        params["days"] = days
    return {"trip_params": params}


async def trip_suggest(state: AgentState):
    msg = state["message"].lower()
    if "where" in msg and ("go" in msg or "can" in msg):
        async with AsyncSessionLocal() as db:
            fb = await flexible_budget(db, state["user_id"])
        available = float(fb["flexible_remaining"])
        currency = state.get("currency", "INR")
        sug = _llm_json(f'Suggest 3 affordable Indian destinations for a short trip with budget {available} {currency}. Return JSON {{"destinations":[{{"name":str,"days":number,"est_cost":number,"transport":str}}]}}.')
        dests = sug.get("destinations") or []
        if not dests:
            return {"reply": f"You have {available:.2f} {currency} flexible. Tell me a destination, days, and budget, and I'll plan it."}
        lines = [f"You have {available:.2f} {currency} flexible. Here are options:"]
        for d in dests:
            lines.append(f"- {d.get('name')}: {d.get('days')} days, est. {d.get('est_cost')} {currency}, transport: {d.get('transport')}")
        lines.append("Pick one and tell me days + budget for a detailed plan.")
        return {"reply": "\n".join(lines)}
    return {}


async def trip_estimate(state: AgentState):
    params = state.get("trip_params", {})
    dest = params.get("destination")
    days = params.get("days") or 2
    budget = params.get("budget")
    transport = params.get("transport")
    currency = state.get("currency", "INR")
    est = _llm_json(f'Estimate costs in {currency} for a {days}-day trip to {dest} by {transport or "road"}. Return JSON {{"travel":number,"stay_per_day":number,"food_per_day":number,"activities_per_day":number,"notes":str}}.')
    travel = float(est.get("travel") or 0)
    stay_pd = float(est.get("stay_per_day") or 0)
    food_pd = float(est.get("food_per_day") or 0)
    act_pd = float(est.get("activities_per_day") or 0)
    notes = est.get("notes") or ""
    total_est = travel + (stay_pd + food_pd + act_pd) * int(days)
    return {"trip_estimate": {"dest": dest, "days": days, "budget": budget, "transport": transport,
                              "travel": travel, "stay_pd": stay_pd, "food_pd": food_pd, "act_pd": act_pd,
                              "notes": notes, "total_est": total_est}}


async def trip_analyze(state: AgentState):
    te = state.get("trip_estimate", {})
    dest = te.get("dest")
    days = te.get("days")
    total_est = te.get("total_est", 0)
    budget = te.get("budget")
    currency = state.get("currency", "INR")
    user_id = state["user_id"]
    async with AsyncSessionLocal() as db:
        fb = await flexible_budget(db, user_id)
        travel_goals = [g for g in (await db.execute(select(Goal).where(Goal.user_id == user_id, Goal.status == "active"))).scalars().all()
                        if "trip" in g.name.lower() or "travel" in g.name.lower()]
        goal_funds = sum(float(g.current_amount or 0) for g in travel_goals)
    available = float(fb["flexible_remaining"]) + goal_funds
    cap = float(budget) if budget else available
    shortfall = total_est - available
    monthly_save = float(fb["monthly_savings"]) if fb["monthly_savings"] else 0
    months_needed = int(-(-shortfall // monthly_save)) if (shortfall > 0 and monthly_save > 0) else 0

    lines = []
    lines.append(f"Here's a friendly plan for your {dest} trip ({days} days):")
    lines.append("")
    lines.append(f"- Available funds (flexible + travel goals): {available:.2f} {currency}")
    lines.append(f"- Estimated total cost: {total_est:.2f} {currency}")
    lines.append(f"  - Travel: {te.get('travel',0):.2f}")
    lines.append(f"  - Stay: {te.get('stay_pd',0):.2f}/day x {days} = {te.get('stay_pd',0)*int(days):.2f}")
    lines.append(f"  - Food: {te.get('food_pd',0):.2f}/day x {days} = {te.get('food_pd',0)*int(days):.2f}")
    lines.append(f"  - Activities: {te.get('act_pd',0):.2f}/day x {days} = {te.get('act_pd',0)*int(days):.2f}")
    if te.get("notes"):
        lines.append(f"- Notes: {te.get('notes')}")
    lines.append(f"- Your budget cap: {cap:.2f} {currency}")
    lines.append(f"- Surplus/(Shortfall): {available - total_est:.2f} {currency}")
    lines.append("")
    if shortfall <= 0:
        lines.append("Good news: you can afford this trip from available funds.")
        if budget and total_est > budget:
            lines.append(f"Estimated cost is {total_est-budget:.2f} above your budget cap; consider cheaper stay/food.")
        lines.append("This won't break your protected savings. Would you like me to create a travel goal to track it?")
    else:
        lines.append(f"You'd be short by {shortfall:.2f} {currency}.")
        lines.append(f"To stay safe, save about {monthly_save:.2f}/month for {months_needed} months before going.")
        lines.append("I can create a savings goal for this trip if you'd like.")
    lines.append("")
    lines.append("Reply 'yes' and I'll set up the goal, or tell me adjustments (fewer days, cheaper stay, etc.).")
    return {"reply": "\n".join(lines), "pending_action": "confirm_trip_goal", "_trip": te}


async def trip_confirm(state: AgentState):
    decision = interrupt({"type": "confirm_trip_goal", "question": state.get("reply")})
    if str(decision).lower() in ("yes", "y", "ok", "okay", "proceed", "confirm"):
        return {"pending_action": "create_trip_goal"}
    return {"reply": "No problem. Tell me if you'd like to adjust the plan.", "pending_action": None}


async def trip_create_goal(state: AgentState):
    te = state.get("_trip") or state.get("trip_estimate", {})
    user_id = state["user_id"]
    currency = state.get("currency", "INR")
    dest = te.get("dest")
    total_est = te.get("total_est", 0)
    async with AsyncSessionLocal() as db:
        g = Goal(user_id=user_id, name=f"Trip to {dest}", goal_type="trip", target_amount=total_est, priority="medium")
        db.add(g)
        await db.commit()
        await db.refresh(g)
    return {"reply": f"Created travel goal 'Trip to {dest}' with target {total_est:.2f} {currency}. I'll track your progress.", "pending_action": None}


async def balance_respond(state: AgentState):
    user_id = state["user_id"]
    currency = state.get("currency", "INR")
    async with AsyncSessionLocal() as db:
        bal = await user_balance(db, user_id)
        fb = await flexible_budget(db, user_id)
    return {"reply": f"Your current balance is {bal} {currency}. Flexible remaining: {fb['flexible_remaining']} {currency}."}


async def general_chat(state: AgentState):
    user_id = state["user_id"]
    currency = state.get("currency", "INR")
    async with AsyncSessionLocal() as db:
        bal = await user_balance(db, user_id)
        fb = await flexible_budget(db, user_id)
    ctx = (f"User financial context (verified): currency={currency}, balance={bal}, "
           f"fixed_monthly_income={fb['month_income']}, extra_income={fb['side_income']}, "
           f"month_expense={fb['month_expense']}, monthly_savings={fb['monthly_savings']}, "
           f"extra_savings={fb['side_savings']}, savings_total={fb['savings_total']}, "
           f"rollover={fb['rollover']}, flexible_remaining={fb['flexible_remaining']}, "
           f"fixed_monthly_expense_estimate={fb['fixed_monthly_expense']}.")
    model = get_chat_model()
    sys = ("You are FinWise AI, an expert personal finance assistant like a friendly bank advisor. "
           "Be concise, warm and helpful. Use tables/bullets when comparing. "
           "Never invent numbers; use the provided context. " + ctx)
    resp = model.invoke([SystemMessage(content=sys), HumanMessage(content=state["message"])])
    return {"reply": resp.content}


def route_after_onboarding(state: AgentState):
    if not state.get("onboarded"):
        return "respond"
    return "classify"


def route_after_classify(state: AgentState):
    intent = state.get("intent")
    if intent == "balance_update":
        return "balance_update_detect"
    if intent == "trip_plan":
        # check suggest vs missing
        if "where" in state["message"].lower() and ("go" in state["message"].lower() or "can" in state["message"].lower()):
            return "trip_suggest"
        return "trip_missing_info"
    if intent == "income":
        return "income_extract"
    if intent == "expense":
        return "expense_extract"
    if intent == "balance":
        return "balance_respond"
    return "general_chat"


def route_after_expense_extract(state: AgentState):
    extracted = state.get("extracted", {})
    txns = extracted.get("transactions") or []
    if not txns:
        return "general_chat"
    return "expense_confirm"


def route_after_expense_confirm(state: AgentState):
    if state.get("pending_action") == "record_expense":
        return "expense_record"
    return "respond"


def route_after_trip_missing(state: AgentState):
    tp = state.get("trip_params", {})
    if not tp.get("destination") or not tp.get("days"):
        return "respond"
    return "trip_estimate"


def route_after_trip_analyze(state: AgentState):
    return "trip_confirm"


def route_after_trip_confirm(state: AgentState):
    if state.get("pending_action") == "create_trip_goal":
        return "trip_create_goal"
    return "respond"


def route_after_balance_update_detect(state: AgentState):
    return "balance_confirm"


def route_after_balance_confirm(state: AgentState):
    if state.get("pending_action") == "apply_balance":
        return "balance_apply"
    return "respond"


# Build graph
builder = StateGraph(AgentState)
builder.add_node("onboarding_check", onboarding_check)
builder.add_node("classify_intent", classify_intent)
builder.add_node("expense_extract", expense_extract)
builder.add_node("income_extract", income_extract)
builder.add_node("expense_confirm", expense_confirm)
builder.add_node("expense_record", expense_record)
builder.add_node("balance_update_detect", balance_update_detect)
builder.add_node("balance_confirm", balance_confirm)
builder.add_node("balance_apply", balance_apply)
builder.add_node("trip_missing_info", trip_missing_info)
builder.add_node("trip_suggest", trip_suggest)
builder.add_node("trip_estimate", trip_estimate)
builder.add_node("trip_analyze", trip_analyze)
builder.add_node("trip_confirm", trip_confirm)
builder.add_node("trip_create_goal", trip_create_goal)
builder.add_node("balance_respond", balance_respond)
builder.add_node("general_chat", general_chat)
builder.add_node("respond", lambda s: s)

builder.add_edge(START, "onboarding_check")
builder.add_conditional_edges("onboarding_check", route_after_onboarding, {"respond": "respond", "classify": "classify_intent"})
builder.add_conditional_edges("classify_intent", route_after_classify, {
    "balance_update_detect": "balance_update_detect",
    "trip_suggest": "trip_suggest",
    "trip_missing_info": "trip_missing_info",
    "expense_extract": "expense_extract",
    "income_extract": "income_extract",
    "balance_respond": "balance_respond",
    "general_chat": "general_chat",
})
builder.add_conditional_edges("expense_extract", route_after_expense_extract, {"expense_confirm": "expense_confirm", "general_chat": "general_chat"})
builder.add_conditional_edges("income_extract", route_after_expense_extract, {"expense_confirm": "expense_confirm", "general_chat": "general_chat"})
builder.add_conditional_edges("expense_confirm", route_after_expense_confirm, {"expense_record": "expense_record", "respond": "respond"})
builder.add_edge("expense_record", "respond")
builder.add_conditional_edges("balance_update_detect", route_after_balance_update_detect, {"balance_confirm": "balance_confirm"})
builder.add_conditional_edges("balance_confirm", route_after_balance_confirm, {"balance_apply": "balance_apply", "respond": "respond"})
builder.add_edge("balance_apply", "respond")
builder.add_conditional_edges("trip_missing_info", route_after_trip_missing, {"respond": "respond", "trip_estimate": "trip_estimate"})
builder.add_edge("trip_suggest", "respond")
builder.add_edge("trip_estimate", "trip_analyze")
builder.add_edge("trip_analyze", "trip_confirm")
builder.add_conditional_edges("trip_confirm", route_after_trip_confirm, {"trip_create_goal": "trip_create_goal", "respond": "respond"})
builder.add_edge("trip_create_goal", "respond")
builder.add_edge("balance_respond", "respond")
builder.add_edge("general_chat", "respond")
builder.add_edge("respond", END)

checkpointer = MemorySaver()
graph = builder.compile(checkpointer=checkpointer)
