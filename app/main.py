from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.api.v1 import auth, transactions, accounts, commitments, goals, dashboard, notifications, documents, reports, chat, profile, savings, income, settings as settings_api

app = FastAPI(title=settings.app_name)

@app.on_event('startup')
async def init_db():
    from app.core.database import Base, engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "ALTER TABLE savings_rules ADD COLUMN IF NOT EXISTS fixed_monthly_expense NUMERIC(14,2) NOT NULL DEFAULT 0"
        ))

origins = [o.strip() for o in settings.cors_origins.split(",")]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, transactions.router, accounts.router, commitments.router, goals.router,
          dashboard.router, notifications.router, documents.router, reports.router, chat.router, profile.router, savings.router, income.router, settings_api.router):
    app.include_router(r, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}
