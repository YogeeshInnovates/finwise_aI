import os, uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.models import UploadedDocument, User

router = APIRouter(prefix="/documents", tags=["documents"])

os.makedirs(settings.upload_dir, exist_ok=True)


@router.post("/upload", status_code=201)
async def upload(file: UploadFile = File(...), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if file.size and file.size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large")
    ext = os.path.splitext(file.filename or "")[1]
    stored = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(settings.upload_dir, stored)
    content = await file.read()
    with open(path, "wb") as f:
        f.write(content)
    doc = UploadedDocument(user_id=user.id, filename=file.filename or stored, content_type=file.content_type,
                           size_bytes=len(content), storage_path=path, extraction_status="pending")
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return {"id": doc.id, "filename": doc.filename, "status": doc.extraction_status}


@router.get("")
async def list_docs(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(UploadedDocument).where(UploadedDocument.user_id == user.id).order_by(UploadedDocument.created_at.desc()))
    return res.scalars().all()
