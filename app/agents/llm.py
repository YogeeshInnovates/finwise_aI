from langchain_groq import ChatGroq

from app.core.config import settings


def get_chat_model(model: str | None = None):
    if not settings.groq_api_key:
        return None
    return ChatGroq(api_key=settings.groq_api_key, model=model or settings.groq_model, temperature=0.2)


def get_extract_model():
    if not settings.groq_api_key:
        return None
    return ChatGroq(api_key=settings.groq_api_key, model=settings.groq_extract_model, temperature=0)
