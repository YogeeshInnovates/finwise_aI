from pinecone import Pinecone, ServerlessSpec

from app.core.config import settings


def _client():
    if not settings.pinecone_api_key:
        return None
    return Pinecone(api_key=settings.pinecone_api_key)


def _index():
    pc = _client()
    if pc is None:
        return None
    name = settings.pinecone_index
    existing = [i.name for i in pc.list_indexes()]
    if name not in existing:
        pc.create_index(name=name, dimension=1024, metric="cosine",
                        spec=ServerlessSpec(cloud="aws", region="us-east-1"))
    return pc.Index(name)


def embed(texts: list[str], input_type: str = "passage"):
    pc = _client()
    if pc is None:
        return None
    res = pc.inference.embed(model="multilingual-e5-large", inputs=texts,
                             parameters={"input_type": input_type, "truncate": "END"})
    return [d.values for d in res.data]


def upsert(user_id: int, source_type: str, source_id: int, chunks: list[str]):
    idx = _index()
    if idx is None:
        return False
    vecs = embed(chunks)
    if vecs is None:
        return False
    items = []
    for i, (chunk, vec) in enumerate(zip(chunks, vecs)):
        vid = f"u{user_id}-{source_type}-{source_id}-{i}"
        items.append({"id": vid, "values": vec, "metadata": {
            "user_id": user_id, "source_type": source_type, "source_id": source_id,
            "text": chunk, "active": True,
        }})
    idx.upsert(vectors=items, namespace=f"user-{user_id}")
    return True


def search(user_id: int, query: str, top_k: int = 5):
    idx = _index()
    if idx is None:
        return []
    qv = embed([query], input_type="query")
    if qv is None:
        return []
    res = idx.query(vector=qv[0], top_k=top_k, namespace=f"user-{user_id}",
                    filter={"active": {"$eq": True}}, include_metadata=True)
    return [{"id": m.id, "score": m.score, "text": m.metadata.get("text")} for m in res.matches]


def deactivate(user_id: int, source_type: str, source_id: int):
    idx = _index()
    if idx is None:
        return False
    # delete by metadata filter
    idx.delete(filter={"user_id": user_id, "source_type": source_type, "source_id": source_id},
               namespace=f"user-{user_id}")
    return True
