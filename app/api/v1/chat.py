from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.models import User
from app.agents.graph import graph

router = APIRouter(prefix="/ai", tags=["ai"])


class ExtractIn(BaseModel):
    text: str


class ChatIn(BaseModel):
    message: str


class SearchIn(BaseModel):
    query: str
    top_k: int = 5


class ResumeIn(BaseModel):
    thread_id: str
    response: str


@router.post("/extract")
async def extract_endpoint(payload: ExtractIn, user: User = Depends(get_current_user)):
    from app.agents.expense_agent import extract
    return extract(payload.text, "INR")


@router.post("/chat")
async def chat(payload: ChatIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    config = {"configurable": {"thread_id": str(user.id)}}
    state = await graph.aget_state(config)
    if state and state.next:
        from langgraph.types import Command
        result = await graph.ainvoke(Command(resume=payload.message), config=config)
        print("DEBUG after resume next=", (await graph.aget_state(config)).next, "reply=", (result.get("reply") if isinstance(result,dict) else result)[:80])
    else:
        result = await graph.ainvoke({"user_id": user.id, "message": payload.message}, config=config)

    # handle interrupt
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupts:
        val = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        q = val.get("question") if isinstance(val, dict) else str(val)
        return {"reply": q, "interrupt": val, "thread_id": str(user.id)}

    reply = result.get("reply") if isinstance(result, dict) else str(result)
    # handle refresh marker
    refresh = "[ACTION:REFRESH]" in (reply or "")
    if refresh:
        reply = reply.replace("[ACTION:REFRESH]", "").strip()
    # frontend refresh via event not possible server-side; marker retained for client
    return {"reply": reply, "thread_id": str(user.id)}


@router.post("/resume")
async def resume(payload: ResumeIn, user: User = Depends(get_current_user)):
    config = {"configurable": {"thread_id": payload.thread_id}}
    from langgraph.types import Command
    result = await graph.ainvoke(Command(resume=payload.response), config=config)
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupts:
        val = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        q = val.get("question") if isinstance(val, dict) else str(val)
        return {"reply": q, "interrupt": val, "thread_id": payload.thread_id}
    reply = result.get("reply") if isinstance(result, dict) else str(result)
    return {"reply": reply, "thread_id": payload.thread_id}


@router.post("/search")
async def semantic_search(payload: SearchIn, user: User = Depends(get_current_user)):
    from app.services.vector import search
    return {"results": search(user.id, payload.query, payload.top_k)}
