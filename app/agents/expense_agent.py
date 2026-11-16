import json
from langchain_core.messages import SystemMessage, HumanMessage

from app.agents.llm import get_extract_model


def extract(text: str, currency: str = "INR", direction: str | None = None) -> dict:
    model = get_extract_model()
    if model is None:
        return {"transactions": [], "needs_confirmation": True, "question": "Groq API key not configured."}
    hint = ""
    if direction == "income":
        hint = (" The user is reporting money they received. Use type 'income' for these amounts, "
                "even if the wording has typos (for example 'gto' means 'got'). Do not treat them as expenses.")
    elif direction == "expense":
        hint = " The user is reporting money they spent. Use type 'expense' for these amounts."
    system = (
        "You extract expense/income transactions from a user message. "
        "Money received by the user (got, received, earned, salary, bonus, profit, freelance, gift, cashback, refund) is type 'income'. "
        "Money the user spent, bought or paid is type 'expense'. "
        f"Default currency is {currency}.{hint} "
        "Return ONLY valid JSON: {\"transactions\":[{\"amount\":number,\"currency\":\"" + currency + "\",\"type\":\"expense|income\",\"category\":str,\"description\":str,\"date\":str|null}],\"needs_confirmation\":bool,\"question\":str|null}. "
        "Never invent amounts. If an amount is missing, set needs_confirmation true and ask. "
        "If all amounts are present, needs_confirmation false."
    )
    resp = model.invoke([SystemMessage(content=system), HumanMessage(content=text)])
    try:
        data = json.loads(resp.content)
    except Exception:
        return {"transactions": [], "needs_confirmation": True, "question": resp.content}
    for t in data.get("transactions", []):
        t.setdefault("currency", currency)
    return data
