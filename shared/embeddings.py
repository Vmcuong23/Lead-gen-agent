"""
Embeddings module — populates `name_embedding` columns on companies and
people, and provides a similarity-search helper for entity resolution.

Two backends, auto-detected at runtime:
  - voyage-3 (1024d) via VOYAGE_API_KEY      — preferred, Anthropic-recommended
  - intfloat/multilingual-e5-large (1024d)   — fallback, runs locally

Both produce 1024-d vectors so the schema stays unchanged.

Why embeddings here:
  Vietnamese company names have wild variants. Trigram similarity catches
  most of them but misses things like "SSDV" ↔ "Samsung SDI Vietnam" or
  "Cty TNHH Bosch Việt Nam" ↔ "Bosch VN". A multilingual embedding model
  groups these by semantic meaning and survives diacritic stripping.
"""
from __future__ import annotations

import logging
import os
import unicodedata
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class _VoyageBackend:
    name = "voyage-3"

    def __init__(self) -> None:
        import voyageai  # type: ignore

        self.client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        # voyage-3 default output is 1024d
        result = self.client.embed(list(texts), model="voyage-3", input_type="document")
        return [list(v) for v in result.embeddings]


class _LocalE5Backend:
    name = "multilingual-e5-large"

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.model = SentenceTransformer("intfloat/multilingual-e5-large")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        # E5 expects a "passage: " or "query: " prefix
        prefixed = [f"passage: {t}" for t in texts]
        vecs = self.model.encode(prefixed, normalize_embeddings=True)
        return [v.tolist() for v in vecs]


class EmbeddingsUnavailable(RuntimeError):
    """Raised when no embedding backend can be loaded (no API key + no local model)."""


_backend = None


def get_backend():
    """Lazy backend selection — Voyage if API key is present, else local."""
    global _backend
    if _backend is not None:
        return _backend
    if os.environ.get("VOYAGE_API_KEY"):
        try:
            _backend = _VoyageBackend()
            logger.info("Using Voyage embedding backend")
            return _backend
        except ImportError:
            logger.warning("voyageai not installed; falling back to local model")
    try:
        _backend = _LocalE5Backend()
    except ImportError as e:
        raise EmbeddingsUnavailable(
            "No embedding backend available. Set VOYAGE_API_KEY or "
            "`pip install sentence-transformers` for the local model."
        ) from e
    logger.info("Using local E5 embedding backend")
    return _backend


# ---------------------------------------------------------------------------
# Name canonicalization for embedding input
# ---------------------------------------------------------------------------
def canonical_name_for_embedding(name: str, *, country: Optional[str] = None) -> str:
    """
    Build the string we feed to the embedding model. We keep diacritics
    (the multilingual models handle them) but strip very generic legal
    suffixes that add noise.
    """
    s = name.strip()
    # Light cleanup only — preserve real semantic content
    junk = [
        " Co., Ltd.", " Co., Ltd", " Co.,Ltd", " Co Ltd",
        " Company Limited", " Limited",
        " JSC", " Joint Stock Company",
        " Inc.", " Inc", " Corp.", " Corporation",
    ]
    for j in junk:
        if s.endswith(j):
            s = s[: -len(j)]
            break
    if country:
        s = f"{s} ({country})"
    return s.strip()


# ---------------------------------------------------------------------------
# Batch embedder for a set of strings
# ---------------------------------------------------------------------------
def embed_batch(texts: Sequence[str], batch_size: int = 64) -> list[list[float]]:
    backend = get_backend()
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        out.extend(backend.embed(chunk))
    return out


# ---------------------------------------------------------------------------
# Database enrichment jobs
# ---------------------------------------------------------------------------
def enrich_company_embeddings(limit: Optional[int] = None) -> int:
    """Populate name_embedding for companies that don't have one yet."""
    from sqlalchemy import select

    from agent1_schema.models import Company
    from agent1_schema.models.db import session_scope

    updated = 0
    with session_scope() as s:
        q = select(Company).where(Company.name_embedding.is_(None))
        if limit:
            q = q.limit(limit)
        companies = list(s.execute(q).scalars())

    # Embed in one batch to amortize API cost
    inputs = [
        canonical_name_for_embedding(c.legal_name, country=c.hq_country)
        for c in companies
    ]
    if not inputs:
        return 0

    vectors = embed_batch(inputs)

    with session_scope() as s:
        for company, vec in zip(companies, vectors):
            db_co = s.get(Company, company.id)
            if db_co is None:
                continue
            db_co.name_embedding = vec
            updated += 1
    logger.info("Embedded %d companies", updated)
    return updated


def enrich_people_embeddings(limit: Optional[int] = None) -> int:
    """Populate name_embedding for people that don't have one yet."""
    from sqlalchemy import select

    from agent1_schema.models import Person
    from agent1_schema.models.db import session_scope

    with session_scope() as s:
        q = (
            select(Person)
            .where(Person.name_embedding.is_(None))
            .where(Person.deleted_at.is_(None))
        )
        if limit:
            q = q.limit(limit)
        people = list(s.execute(q).scalars())

    if not people:
        return 0

    inputs = [f"{p.full_name} — {p.title}" for p in people]
    vectors = embed_batch(inputs)

    updated = 0
    with session_scope() as s:
        for person, vec in zip(people, vectors):
            db_p = s.get(Person, person.id)
            if db_p is None:
                continue
            db_p.name_embedding = vec
            updated += 1
    logger.info("Embedded %d people", updated)
    return updated


# ---------------------------------------------------------------------------
# Similarity search — used by Agent 2's entity resolver to catch dups that
# trigram similarity misses.
# ---------------------------------------------------------------------------
def find_similar_companies(
    name: str,
    *,
    country: Optional[str] = None,
    threshold: float = 0.85,
    limit: int = 5,
) -> list[tuple[str, str, float]]:
    """
    Returns [(company_id, legal_name, cosine_similarity)] for companies whose
    name embedding is close to `name`. Used for fuzzy entity resolution.
    """
    from sqlalchemy import text

    from agent1_schema.models.db import session_scope

    [vector] = embed_batch([canonical_name_for_embedding(name, country=country)])

    with session_scope() as s:
        rows = s.execute(
            text(
                """
                SELECT id::text AS id, legal_name,
                       1 - (name_embedding <=> CAST(:vec AS vector)) AS sim
                FROM companies
                WHERE name_embedding IS NOT NULL
                ORDER BY name_embedding <=> CAST(:vec AS vector)
                LIMIT :limit
                """
            ),
            {"vec": str(vector), "limit": limit},
        ).all()

    return [(r.id, r.legal_name, float(r.sim)) for r in rows if r.sim >= threshold]
