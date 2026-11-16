from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
url = settings.database_url.replace("postgresql+asyncpg","postgresql+psycopg2").replace("postgresql://","postgresql+psycopg2://")
_sync_engine = create_engine(url)
SessionLocal = sessionmaker(bind=_sync_engine)
from app.models.models import FixedCommitment, Notification, User


@celery_app.task
def check_upcoming_bills():
    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        soon = now + timedelta(days=3)
        commits = db.query(FixedCommitment).filter(
            FixedCommitment.status == "unpaid",
            FixedCommitment.due_date >= now,
            FixedCommitment.due_date <= soon,
        ).all()
        for c in commits:
            n = Notification(user_id=c.user_id, ntype="bill_due", title="Upcoming bill",
                             body=f"{c.name} of {c.amount} is due on {c.due_date.date()}.")
            db.add(n)
        db.commit()
        return len(commits)
    finally:
        db.close()
