import json
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from dateutil import parser as date_parser
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.llm import get_chat_model, get_extract_model

GOAL_TYPES = {"trip", "purchase", "event", "education", "emergency", "other"}
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".log"}


def _json_from_text(text: str) -> dict:
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _invoke_json(model, prompt: str) -> dict:
    if model is None:
        return {}
    try:
        resp = model.invoke([
            SystemMessage(content="You are a precise financial planning assistant. Return ONLY valid JSON. No markdown, no commentary."),
            HumanMessage(content=prompt),
        ])
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        return _json_from_text(content)
    except Exception:
        return {}


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except Exception:
        return None


def _date_string(value):
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    try:
        return date_parser.parse(str(value)).date().isoformat()
    except Exception:
        return None


def _text(value, limit=2000):
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned[:limit] if cleaned else None


def extract_text(path: str, content_type: str | None = None, filename: str | None = None) -> str:
    ext = Path(filename or path).suffix.lower()
    if ext == ".pdf" or content_type == "application/pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    if ext in TEXT_EXTENSIONS or (content_type or "").startswith("text/"):
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    raise ValueError("Unsupported file type. Upload a PDF, TXT, MD, CSV, or JSON file.")


def chunk_text(text: str, size: int = 1200, overlap: int = 200, max_chunks: int = 24) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return []
    chunks = []
    start = 0
    while start < len(cleaned) and len(chunks) < max_chunks:
        end = min(len(cleaned), start + size)
        chunks.append(cleaned[start:end])
        if end >= len(cleaned):
            break
        start = max(end - overlap, start + 1)
    return chunks


def normalize_details(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    name = _text(raw.get("name") or raw.get("title"), 255)
    if name:
        out["name"] = name
    raw_type = str(raw.get("goal_type") or "").strip().lower()
    if raw_type in GOAL_TYPES:
        out["goal_type"] = raw_type
    elif raw.get("destination") or raw.get("transport") or raw.get("current_location"):
        out["goal_type"] = "trip"
    for key in ("current_location", "destination", "transport", "preferences", "notes"):
        value = _text(raw.get(key))
        if value:
            out[key] = value
    for key in ("days", "travelers"):
        value = _num(raw.get(key))
        if value is not None and value > 0:
            out[key] = int(value)
    for key in ("start_date", "target_date"):
        value = _date_string(raw.get(key))
        if value:
            out[key] = value
    budget = _num(raw.get("budget") or raw.get("target_amount"))
    if budget is not None and budget > 0:
        out["budget"] = budget
    priority = str(raw.get("priority") or "").strip().lower()
    if priority in {"low", "medium", "high"}:
        out["priority"] = priority
    return out


def extract_plan_details(text: str) -> dict:
    if not text.strip():
        return {}
    prompt = (
        "Extract plan details from the user's document. The document may describe a trip, purchase, "
        "event, education, emergency, or another expenditure plan.\n"
        "Return JSON with these keys (use null when unknown):\n"
        '{"name":str,"goal_type":"trip"|"purchase"|"event"|"education"|"emergency"|"other",'
        '"current_location":str,"destination":str,"days":number,"travelers":number,"start_date":"YYYY-MM-DD",'
        '"target_date":"YYYY-MM-DD","budget":number,"transport":str,"preferences":str,"notes":str}\n'
        "Use INR amounts. Do not invent details that are not supported by the document.\n\n"
        f"DOCUMENT:\n{text[:14000]}"
    )
    return normalize_details(_invoke_json(get_extract_model(), prompt))


def _months_until(target_date: str | None) -> int:
    if not target_date:
        return 0
    try:
        target = date_parser.parse(str(target_date)).date()
    except Exception:
        return 0
    today = datetime.now(timezone.utc).date()
    if target <= today:
        return 1
    return max(1, (target.year - today.year) * 12 + target.month - today.month + (1 if target.day > today.day else 0))


def fallback_plan(details: dict, financial: dict) -> dict:
    goal_type = details.get("goal_type") or "other"
    currency = financial.get("currency") or "INR"
    budget = _num(details.get("budget")) or 0.0
    days = max(1, int(_num(details.get("days")) or 3))
    travelers = max(1, int(_num(details.get("travelers")) or 1))
    itinerary = []
    breakdown = []

    if goal_type == "trip":
        destination = details.get("destination")
        current_location = details.get("current_location")
        transport = (details.get("transport") or "").lower()
        if "flight" in transport or "plane" in transport:
            travel = 6000.0 * travelers
        elif "train" in transport:
            travel = 1500.0 * travelers
        elif "bus" in transport:
            travel = 1000.0 * travelers
        elif "bike" in transport or "car" in transport:
            travel = 2500.0
        else:
            travel = 3000.0
        rooms = math.ceil(travelers / 2)
        stay = 1500.0 * rooms * days
        food = 700.0 * travelers * days
        activities = 500.0 * travelers * days
        local = 300.0 * days
        base = travel + stay + food + activities + local
        misc = round(base * 0.05, 2)
        estimated_total = round(base + misc, 2)
        breakdown = [
            {"label": "Travel", "amount": round(travel, 2), "note": "To and from the destination"},
            {"label": "Stay", "amount": round(stay, 2), "note": f"{rooms} room(s) x {days} night(s)"},
            {"label": "Food", "amount": round(food, 2), "note": f"{travelers} traveler(s) x {days} day(s)"},
            {"label": "Activities", "amount": round(activities, 2), "note": "Sightseeing and experiences"},
            {"label": "Local transport", "amount": round(local, 2), "note": "Local travel at the destination"},
            {"label": "Buffer", "amount": misc, "note": "Emergency and miscellaneous spending"},
        ]
        for day in range(1, days + 1):
            if not destination:
                break
            if day == 1:
                title = "Arrival and settle in"
                acts = ["Reach the destination", "Check in and rest", "Explore a nearby area"]
            elif day == days:
                title = "Last experiences and return"
                acts = ["Pack and check out", "Visit one final attraction", "Travel back home"]
            else:
                title = f"Explore {destination}"
                acts = ["Visit a main attraction", "Try local food", "Evening sightseeing"]
            itinerary.append({
                "day": day,
                "title": title,
                "activities": acts,
                "estimated_cost": round((stay + food + activities + local) / days, 2),
            })
        if destination:
            summary = f"A {days}-day trip to {destination} for {travelers} traveler(s), estimated at {estimated_total:.2f} {currency}."
        elif current_location:
            summary = f"A {days}-day trip from {current_location} for {travelers} traveler(s). Pick one of the affordable places below to start planning."
        else:
            summary = f"A {days}-day trip for {travelers} traveler(s). Pick one of the affordable places below to start planning."
    else:
        estimated_total = round(budget, 2)
        label = details.get("name") or goal_type.replace("_", " ").title()
        breakdown = [{
            "label": label,
            "amount": estimated_total,
            "note": "Estimated from the budget you entered" if estimated_total else "Add a budget for a precise estimate",
        }]
        summary = f"A plan for {label}" + (f", estimated at {estimated_total:.2f} {currency}." if estimated_total else ". Add a budget to get a sharper estimate.")

    return {
        "summary": summary,
        "estimated_total": estimated_total,
        "breakdown": breakdown,
        "itinerary": itinerary,
        "suggested_destinations": [],
        "affordability": {},
        "tips": [
            "Review the estimate and adjust the plan if prices are different in your area.",
            "Keep a small buffer for unexpected costs.",
            "Track actual spending against this plan as the date gets closer.",
        ],
        "assumptions": ["Estimates use typical costs and may vary by season and location."],
    }


def _verdict(estimated_total: float, available: float) -> str:
    if available <= 0:
        return "not_affordable"
    if estimated_total <= available * 0.8:
        return "affordable"
    if estimated_total <= available:
        return "tight"
    return "not_affordable"


def _normalize_suggestions(raw, details: dict) -> list[dict]:
    items = []
    raw_list = raw if isinstance(raw, list) else []
    default_days = max(1, int(_num(details.get("days")) or 2))
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), 140)
        if not name:
            continue
        items.append({
            "name": name,
            "days": max(1, int(_num(item.get("days")) or default_days)),
            "est_cost": round(max(0.0, _num(item.get("est_cost")) or 0.0), 2),
            "transport": _text(item.get("transport"), 80) or "",
            "why": _text(item.get("why"), 300) or "",
        })
    return items[:4]


def _suggest_destinations(details: dict, financial: dict, available: float) -> list[dict]:
    location = details.get("current_location") or details.get("destination") or "the user's city"
    days = max(1, int(_num(details.get("days")) or 2))
    travelers = max(1, int(_num(details.get("travelers")) or 1))
    currency = financial.get("currency") or "INR"
    comfortable = round(max(0.0, available) * 0.8, 2)
    prompt = (
        "Suggest realistic travel destinations for this user. Return ONLY JSON.\n"
        f"Current location: {location}\n"
        f"Trip length: {days} day(s)\n"
        f"Travelers: {travelers}\n"
        f"Comfortable total spend: {comfortable:.2f} {currency}\n"
        f"Hard spending cap: {max(0.0, available):.2f} {currency}\n"
        "Rules:\n"
        "- Suggest 3 places reachable from the current location by train, bus, or car unless a flight fits the budget.\n"
        "- Keep the total estimated cost at or below the hard cap when possible.\n"
        "- If the budget is very low, suggest nearby or free/low-cost places.\n"
        "- Give a short, warm reason for each place.\n"
        'Return {"suggested_destinations":[{"name":str,"days":number,"est_cost":number,'
        '"transport":str,"why":str}]}'
    )
    raw = _invoke_json(get_chat_model(), prompt)
    suggestions = raw.get("suggested_destinations") or raw.get("destinations") or []
    return _normalize_suggestions(suggestions, details)


def normalize_plan(raw: dict, details: dict, financial: dict) -> dict:
    fallback = fallback_plan(details, financial)
    plan = raw if isinstance(raw, dict) else {}

    summary = _text(plan.get("summary"), 1200) or fallback["summary"]
    if not details.get("destination") and "your destination" in summary.lower():
        summary = fallback["summary"]
    estimated_total = _num(plan.get("estimated_total"))
    if estimated_total is None or estimated_total <= 0:
        estimated_total = fallback["estimated_total"]

    breakdown = []
    raw_breakdown = plan.get("breakdown") if isinstance(plan.get("breakdown"), list) else []
    for item in raw_breakdown:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("label"), 120)
        amount = _num(item.get("amount"))
        if not label or amount is None:
            continue
        breakdown.append({"label": label, "amount": round(max(0.0, amount), 2), "note": _text(item.get("note"), 300) or ""})
    if not breakdown:
        breakdown = fallback["breakdown"]

    itinerary = []
    raw_itinerary = plan.get("itinerary") if isinstance(plan.get("itinerary"), list) else []
    for index, item in enumerate(raw_itinerary):
        if not isinstance(item, dict):
            continue
        activities = item.get("activities") if isinstance(item.get("activities"), list) else []
        itinerary.append({
            "day": int(_num(item.get("day")) or index + 1),
            "title": _text(item.get("title"), 180) or f"Day {index + 1}",
            "activities": [_text(a, 240) for a in activities if _text(a, 240)],
            "estimated_cost": round(max(0.0, _num(item.get("estimated_cost")) or 0.0), 2),
        })
    if not itinerary and (details.get("goal_type") == "trip"):
        itinerary = fallback["itinerary"]

    suggested_destinations = _normalize_suggestions(
        plan.get("suggested_destinations") if isinstance(plan.get("suggested_destinations"), list) else [],
        details,
    )

    available = float(financial.get("flexible_remaining") or 0)
    surplus = round(available - estimated_total, 2)
    shortfall = max(0.0, -surplus)
    target_months = _months_until(details.get("target_date"))
    raw_aff = plan.get("affordability") if isinstance(plan.get("affordability"), dict) else {}
    months = int(_num(raw_aff.get("months_to_goal")) or 0)
    savings_pace = float(financial.get("monthly_savings") or 0)
    if shortfall > 0:
        if months <= 0:
            if target_months:
                months = target_months
            elif savings_pace > 0:
                months = max(1, math.ceil(shortfall / savings_pace))
            else:
                months = 3
        monthly_needed = round(shortfall / months, 2)
    else:
        months = 0
        monthly_needed = 0.0
    monthly_capacity = savings_pace
    if monthly_capacity <= 0:
        monthly_capacity = float(financial.get("month_income") or 0) * 0.1

    raw_advice = raw_aff.get("advice") if isinstance(raw_aff.get("advice"), list) else []
    advice = [_text(a, 400) for a in raw_advice if _text(a, 400)]
    verdict = _verdict(estimated_total, available)
    if not suggested_destinations and details.get("goal_type") == "trip" and (not details.get("destination") or verdict != "affordable"):
        suggested_destinations = _suggest_destinations(details, financial, available)
    if not advice:
        if verdict == "affordable":
            advice = ["This plan fits within your flexible funds. Keep the buffer untouched until the plan is complete."]
        elif verdict == "tight":
            advice = ["This plan is possible but leaves little room for surprises. Trim one or two non-essential costs."]
        else:
            advice = [
                f"You are short by {shortfall:.2f} {financial.get('currency') or 'INR'}. Consider a smaller plan or a later date.",
                f"Save about {monthly_needed:.2f} per month for {months or 1} month(s) to close the gap.",
            ]
    if shortfall > 0 and monthly_capacity > 0 and monthly_needed > monthly_capacity:
        advice.append(f"Your current savings pace is about {monthly_capacity:.2f}/month, so increase it or extend the timeline.")

    tips = [_text(t, 400) for t in (plan.get("tips") if isinstance(plan.get("tips"), list) else []) if _text(t, 400)]
    if not tips:
        tips = fallback["tips"]
    assumptions = [_text(a, 400) for a in (plan.get("assumptions") if isinstance(plan.get("assumptions"), list) else []) if _text(a, 400)]
    if not assumptions:
        assumptions = fallback["assumptions"]

    return {
        "summary": summary,
        "estimated_total": round(estimated_total, 2),
        "breakdown": breakdown,
        "itinerary": itinerary,
        "suggested_destinations": suggested_destinations,
        "affordability": {
            "available_funds": round(available, 2),
            "recommended_spend": round(max(0.0, available * 0.8), 2),
            "max_spend": round(max(0.0, available), 2),
            "surplus_or_shortfall": surplus,
            "verdict": verdict,
            "monthly_saving_needed": monthly_needed,
            "months_to_goal": months,
            "advice": advice,
        },
        "tips": tips,
        "assumptions": assumptions,
    }


def generate_plan(details: dict, financial: dict, document_text: str = "", rag_sources: list | None = None) -> dict:
    rag_sources = rag_sources or []
    currency = financial.get("currency") or "INR"
    rag_text = "\n\n".join(
        f"- {item.get('text')}" for item in rag_sources if isinstance(item, dict) and item.get("text")
    )
    prompt = (
        "Create a practical expenditure plan for this user.\n\n"
        f"PLAN DETAILS:\n{json.dumps(details, ensure_ascii=False, default=str)}\n\n"
        f"VERIFIED FINANCIAL CONTEXT:\n{json.dumps(financial, ensure_ascii=False, default=str)}\n\n"
        f"USER'S UPLOADED PLAN DOCUMENT:\n{document_text[:9000] if document_text else 'None'}\n\n"
        f"RELATED NOTES FROM THE USER'S DOCUMENTS:\n{rag_text[:4000] if rag_text else 'None'}\n\n"
        "Rules:\n"
        f"- Use {currency} for all amounts.\n"
        "- For trips, estimate travel, stay, food, activities, local transport, and a buffer.\n"
        "- For non-trip goals, create realistic cost components and milestones instead of a day-by-day itinerary.\n"
        "- Respect the user's budget cap if one is provided.\n"
        "- Use current_location to estimate travel distance and costs when available.\n"
        "- If destination is missing, suggest 3 realistic destinations reachable from current_location, "
        "with transport, estimated cost, days, and a short reason. These should be affordable options the user can choose.\n"
        "- If the plan is not affordable, include 2-3 cheaper alternative destinations when possible.\n"
        "- Use only the verified financial context for affordability. Available funds are flexible_remaining.\n"
        "- Do not recommend spending protected savings.\n"
        "- If the plan is not affordable, suggest a cheaper version or a realistic savings timeline.\n"
        "- Make the itinerary specific to the destination and preferences when possible.\n"
        "- Return ONLY JSON with this exact shape:\n"
        '{"summary":str,"estimated_total":number,'
        '"breakdown":[{"label":str,"amount":number,"note":str}],'
        '"itinerary":[{"day":number,"title":str,"activities":[str],"estimated_cost":number}],'
        '"suggested_destinations":[{"name":str,"days":number,"est_cost":number,"transport":str,"why":str}],'
        '"affordability":{"available_funds":number,"recommended_spend":number,"max_spend":number,'
        '"surplus_or_shortfall":number,"verdict":"affordable"|"tight"|"not_affordable",'
        '"monthly_saving_needed":number,"months_to_goal":number,"advice":[str]},'
        '"tips":[str],"assumptions":[str]}'
        "\nSet recommended_spend to about 80% of available funds (a comfortable spend). "
        "Set max_spend to the available funds."
    )
    raw = _invoke_json(get_chat_model(), prompt)
    return normalize_plan(raw, details, financial)
