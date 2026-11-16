import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.models import (
    BankAccount,
    Goal,
    GoalContribution,
    PlanExtraction,
    UploadedDocument,
    User,
)
from app.services import vector
from app.services.finance import flexible_budget, user_balance
from app.services.planning import (
    GOAL_TYPES,
    TEXT_EXTENSIONS,
    chunk_text,
    extract_plan_details,
    extract_text,
    generate_plan,
)

router = APIRouter(prefix="/goals", tags=["goals"])

os.makedirs(settings.upload_dir, exist_ok=True)

TYPE_LABELS = {
    "trip": "Trip",
    "purchase": "Purchase",
    "event": "Event",
    "education": "Education",
    "emergency": "Emergency",
    "other": "Plan",
}


class GoalIn(BaseModel):
    name: str
    goal_type: str = "trip"
    target_amount: Decimal
    target_date: datetime | None = None
    priority: str = "medium"


class ContribIn(BaseModel):
    amount: Decimal
    direction: str = "in"


class PlanIn(BaseModel):
    goal_id: int | None = None
    name: str | None = None
    goal_type: str = "trip"
    current_location: str | None = None
    destination: str | None = None
    days: int | None = None
    travelers: int | None = None
    start_date: datetime | None = None
    target_date: datetime | None = None
    budget: Decimal | None = None
    transport: str | None = None
    preferences: str | None = None
    notes: str | None = None
    priority: str = "medium"
    document_id: int | None = None
    use_rag: bool = True


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _default_name(details: dict) -> str:
    goal_type = details.get("goal_type") or "other"
    if goal_type == "trip":
        destination = details.get("destination")
        if destination:
            return f"Trip to {destination}"
        current_location = details.get("current_location")
        return f"Trip plan from {current_location}" if current_location else "Trip plan"
    return f"{TYPE_LABELS.get(goal_type, 'Plan')} plan"


async def _latest_extraction(db: AsyncSession, user_id: int, goal_id: int):
    res = await db.execute(
        select(PlanExtraction)
        .where(PlanExtraction.user_id == user_id, PlanExtraction.goal_id == goal_id)
        .order_by(PlanExtraction.created_at.desc(), PlanExtraction.id.desc())
    )
    return res.scalars().first()


def _goal_dict(goal: Goal, extraction: PlanExtraction | None, document: UploadedDocument | None) -> dict:
    data = extraction.extracted_json if extraction and isinstance(extraction.extracted_json, dict) else {}
    details = data.get("input") if isinstance(data.get("input"), dict) else {}
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    financial = data.get("financial") if isinstance(data.get("financial"), dict) else {}
    rag_sources = data.get("rag_sources") if isinstance(data.get("rag_sources"), list) else []
    target = float(goal.target_amount or 0)
    current = float(goal.current_amount or 0)
    progress = round(min(100.0, (current / target) * 100), 1) if target > 0 else 0.0
    return {
        "id": goal.id,
        "name": goal.name,
        "goal_type": goal.goal_type,
        "target_amount": target,
        "current_amount": current,
        "target_date": goal.target_date.isoformat() if goal.target_date else None,
        "priority": goal.priority,
        "status": goal.status,
        "created_at": goal.created_at.isoformat() if goal.created_at else None,
        "progress_percent": progress,
        "details": details,
        "plan": plan,
        "financial": financial,
        "rag_sources": rag_sources,
        "document_id": extraction.document_id if extraction else None,
        "document_name": document.filename if document else data.get("document_name"),
    }


async def _financial_context(db: AsyncSession, user: User) -> dict:
    fb = await flexible_budget(db, user.id)
    balance = await user_balance(db, user.id)
    res = await db.execute(select(BankAccount).where(BankAccount.user_id == user.id))
    account = res.scalars().first()
    currency = (account.currency if account and account.currency else "INR")
    return {"currency": currency, "balance": float(balance), **fb}


async def _read_document(db: AsyncSession, user: User, document_id: int | None):
    if not document_id:
        return None, ""
    res = await db.execute(
        select(UploadedDocument).where(UploadedDocument.id == document_id, UploadedDocument.user_id == user.id)
    )
    document = res.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="plan document not found")
    text = ""
    if document.storage_path and os.path.exists(document.storage_path):
        try:
            text = extract_text(document.storage_path, document.content_type, document.filename)
        except Exception:
            text = ""
    return document, text


async def _cleanup_document(db: AsyncSession, user: User, document_id: int | None):
    if not document_id:
        return
    refs = await db.execute(
        select(PlanExtraction).where(
            PlanExtraction.user_id == user.id,
            PlanExtraction.document_id == document_id,
        )
    )
    if refs.scalars().first() is not None:
        return
    dres = await db.execute(
        select(UploadedDocument).where(
            UploadedDocument.id == document_id,
            UploadedDocument.user_id == user.id,
        )
    )
    document = dres.scalar_one_or_none()
    if not document:
        return
    try:
        vector.deactivate(user.id, "plan", document_id)
    except Exception:
        pass
    path = document.storage_path
    await db.delete(document)
    await db.commit()
    if path:
        try:
            os.remove(path)
        except Exception:
            pass


@router.get("")
async def list_goals(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(
        select(Goal).where(Goal.user_id == user.id).order_by(Goal.created_at.desc(), Goal.id.desc())
    )
    goals = res.scalars().all()
    if not goals:
        return []
    goal_ids = [goal.id for goal in goals]
    eres = await db.execute(
        select(PlanExtraction)
        .where(PlanExtraction.user_id == user.id, PlanExtraction.goal_id.in_(goal_ids))
        .order_by(PlanExtraction.created_at.desc(), PlanExtraction.id.desc())
    )
    by_goal = {}
    for extraction in eres.scalars().all():
        by_goal.setdefault(extraction.goal_id, extraction)
    doc_ids = {extraction.document_id for extraction in by_goal.values() if extraction.document_id}
    documents = {}
    if doc_ids:
        dres = await db.execute(
            select(UploadedDocument).where(UploadedDocument.id.in_(doc_ids), UploadedDocument.user_id == user.id)
        )
        documents = {doc.id: doc for doc in dres.scalars().all()}
    result = []
    for goal in goals:
        extraction = by_goal.get(goal.id)
        document = documents.get(extraction.document_id) if extraction and extraction.document_id else None
        result.append(_goal_dict(goal, extraction, document))
    return result


@router.post("", status_code=201)
async def create_goal(payload: GoalIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    goal = Goal(
        user_id=user.id,
        name=payload.name,
        goal_type=payload.goal_type,
        target_amount=payload.target_amount,
        target_date=_aware(payload.target_date),
        priority=payload.priority,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return _goal_dict(goal, None, None)


@router.post("/plan-document", status_code=201)
async def upload_plan_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filename = file.filename or "plan"
    ext = Path(filename).suffix.lower()
    if ext != ".pdf" and ext not in TEXT_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Upload a PDF, TXT, MD, CSV, or JSON file.",
        )
    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large")
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(settings.upload_dir, stored)
    with open(path, "wb") as handle:
        handle.write(content)

    document = UploadedDocument(
        user_id=user.id,
        filename=filename,
        content_type=file.content_type,
        size_bytes=len(content),
        storage_path=path,
        extraction_status="processing",
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    try:
        text = extract_text(path, file.content_type, filename)
    except Exception as exc:
        document.extraction_status = "failed"
        await db.commit()
        raise HTTPException(status_code=422, detail=f"Could not read this document: {exc}")

    if not text.strip():
        document.extraction_status = "empty"
        await db.commit()
        return {
            "document_id": document.id,
            "filename": document.filename,
            "status": document.extraction_status,
            "characters": 0,
            "chunks": 0,
            "rag_enabled": False,
            "extracted": {},
            "preview": "",
        }

    chunks = chunk_text(text)
    rag_enabled = False
    try:
        rag_enabled = bool(vector.upsert(user.id, "plan", document.id, chunks))
    except Exception:
        rag_enabled = False

    extracted = extract_plan_details(text)
    document.extraction_status = "indexed" if rag_enabled else "text_extracted"
    await db.commit()

    return {
        "document_id": document.id,
        "filename": document.filename,
        "status": document.extraction_status,
        "characters": len(text),
        "chunks": len(chunks),
        "rag_enabled": rag_enabled,
        "extracted": extracted,
        "preview": text[:600],
    }


@router.post("/plan")
async def create_plan(payload: PlanIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    details = payload.model_dump(exclude_none=True)
    for key in ("goal_id", "document_id", "use_rag"):
        details.pop(key, None)
    if details.get("goal_type") not in GOAL_TYPES:
        details["goal_type"] = "trip" if details.get("destination") else "other"
    for key in ("start_date", "target_date"):
        value = details.get(key)
        if isinstance(value, datetime):
            details[key] = value.date().isoformat()
    if isinstance(details.get("budget"), Decimal):
        details["budget"] = float(details["budget"])
    if details.get("days") is not None:
        details["days"] = int(details["days"])
    if details.get("travelers") is not None:
        details["travelers"] = int(details["travelers"])

    financial = await _financial_context(db, user)
    document, document_text = await _read_document(db, user, payload.document_id)

    rag_sources = []
    if payload.use_rag:
        query = " ".join(
            str(value)
            for value in (
                details.get("name"),
                details.get("current_location"),
                details.get("destination"),
                details.get("goal_type"),
                details.get("preferences"),
                details.get("notes"),
            )
            if value
        )
        if query:
            try:
                rag_sources = vector.search(user.id, query, top_k=5)
            except Exception:
                rag_sources = []

    plan = generate_plan(details, financial, document_text, rag_sources)

    if payload.goal_id:
        res = await db.execute(select(Goal).where(Goal.id == payload.goal_id, Goal.user_id == user.id))
        goal = res.scalar_one_or_none()
        if not goal:
            raise HTTPException(status_code=404, detail="goal not found")
    else:
        goal = Goal(user_id=user.id, status="active")
        db.add(goal)

    goal.name = details.get("name") or _default_name(details)
    goal.goal_type = details.get("goal_type") or "other"
    goal.target_amount = Decimal(str(plan.get("estimated_total") or details.get("budget") or 0))
    if payload.target_date is not None or payload.goal_id is None:
        goal.target_date = _aware(payload.target_date)
    if payload.priority in {"low", "medium", "high"}:
        goal.priority = payload.priority
    await db.flush()

    extraction = await _latest_extraction(db, user.id, goal.id) if payload.goal_id else None
    if not extraction:
        extraction = PlanExtraction(user_id=user.id, goal_id=goal.id)
        db.add(extraction)
    previous_document_id = extraction.document_id
    extraction.document_id = payload.document_id
    extraction.confirmed = True
    extraction.extracted_json = {
        "input": details,
        "plan": plan,
        "financial": financial,
        "rag_sources": rag_sources,
        "document_name": document.filename if document else None,
    }
    await db.commit()
    await db.refresh(goal)
    response = {
        "goal": _goal_dict(goal, extraction, document),
        "plan": plan,
        "details": details,
        "financial": financial,
        "rag_sources": rag_sources,
        "document_name": document.filename if document else None,
    }
    if previous_document_id and previous_document_id != payload.document_id:
        await _cleanup_document(db, user, previous_document_id)
    return response


@router.post("/{gid}/contribute")
async def contribute(
    gid: int,
    payload: ContribIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await db.execute(select(Goal).where(Goal.id == gid, Goal.user_id == user.id))
    goal = res.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="goal not found")
    contribution = GoalContribution(goal_id=goal.id, amount=payload.amount, direction=payload.direction)
    db.add(contribution)
    if payload.direction == "in":
        goal.current_amount = (goal.current_amount or Decimal("0")) + payload.amount
    else:
        goal.current_amount = (goal.current_amount or Decimal("0")) - payload.amount
    await db.commit()
    return {"goal_id": goal.id, "current_amount": float(goal.current_amount)}


@router.patch("/{gid}/status")
async def set_status(
    gid: int,
    status: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if status not in {"active", "completed", "cancelled", "archived"}:
        raise HTTPException(status_code=400, detail="invalid status")
    res = await db.execute(select(Goal).where(Goal.id == gid, Goal.user_id == user.id))
    goal = res.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="goal not found")
    goal.status = status
    await db.commit()
    return {"status": goal.status}


@router.delete("/plan-document/{document_id}", status_code=204)
async def delete_plan_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await db.execute(
        select(UploadedDocument).where(
            UploadedDocument.id == document_id,
            UploadedDocument.user_id == user.id,
        )
    )
    document = res.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="plan document not found")

    eres = await db.execute(
        select(PlanExtraction).where(
            PlanExtraction.user_id == user.id,
            PlanExtraction.document_id == document_id,
        )
    )
    for extraction in eres.scalars().all():
        extraction.document_id = None
        data = extraction.extracted_json if isinstance(extraction.extracted_json, dict) else {}
        if isinstance(data, dict):
            data.pop("document_name", None)
            extraction.extracted_json = data
    await db.flush()

    try:
        vector.deactivate(user.id, "plan", document_id)
    except Exception:
        pass
    path = document.storage_path
    await db.delete(document)
    await db.commit()
    if path:
        try:
            os.remove(path)
        except Exception:
            pass
    return Response(status_code=204)


@router.delete("/{gid}", status_code=204)
async def delete_goal(
    gid: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await db.execute(select(Goal).where(Goal.id == gid, Goal.user_id == user.id))
    goal = res.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="goal not found")
    eres = await db.execute(select(PlanExtraction).where(PlanExtraction.goal_id == goal.id))
    extractions = eres.scalars().all()
    document_ids = {extraction.document_id for extraction in extractions if extraction.document_id}
    for extraction in extractions:
        await db.delete(extraction)
    cres = await db.execute(select(GoalContribution).where(GoalContribution.goal_id == goal.id))
    for contribution in cres.scalars().all():
        await db.delete(contribution)
    await db.delete(goal)
    await db.commit()
    for document_id in document_ids:
        await _cleanup_document(db, user, document_id)
    return Response(status_code=204)
