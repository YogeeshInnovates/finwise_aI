from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# ensure async driver
url = settings.database_url
if url.startswith("postgresql+psycopg2"):
    url = url.replace("postgresql+psycopg2", "postgresql+asyncpg")
elif url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
