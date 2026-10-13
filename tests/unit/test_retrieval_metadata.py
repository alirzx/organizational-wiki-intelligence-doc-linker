from app.api.schemas import SearchRequest
from app.core.config import Settings
from app.services.retrieval import RetrievalService
from app.vector.qdrant_store import RetrievedPoint


class Embedder:
    def encode_query(self, _query):
        return [1.0], [], []


class Reranker:
    def score(self, _query, passages):
        return [0.9 for _ in passages]


class Vector:
    def __init__(self, payload):
        self.payload = payload

    def ensure_collection(self):
        pass

    def search(self, **_kwargs):
        return [RetrievedPoint("point-1", 0.5, self.payload)]


def _search(payload):
    settings = Settings(_env_file=None, qdrant_vector_size=1)
    service = RetrievalService(settings, Embedder(), Reranker(), Vector(payload))
    return service.search(SearchRequest(
        query="policy", organization_id="org-1", wiki_id="wiki-1"
    )).results[0]


def _payload():
    return {
        "text": "Published paragraph",
        "document_id": "doc-1",
        "document_version": "3",
        "document_title": "Policy",
        "content_id": "paragraph-17",
        "content_type": "paragraph",
        "content_title": "Published content",
        "content_order": 17,
        "source_metadata": {},
        "chunk_index": 0,
        "artifact_bucket": "media",
        "artifact_key": "documents/doc-1/versions/v3/document.json",
        "content_hash": "a" * 64,
    }


def test_published_search_result_preserves_null_legacy_section_fields():
    result = _search(_payload())
    assert result.content_id == "paragraph-17"
    assert result.content_type == "paragraph"
    assert result.section_id is None
    assert result.section_title is None
    assert result.section_order is None


def test_legacy_search_result_preserves_real_section_fields():
    payload = _payload()
    payload.update({
        "section_id": "section-1",
        "section_title": "Legacy section",
        "section_order": 4,
    })
    result = _search(payload)
    assert result.section_id == "section-1"
    assert result.section_title == "Legacy section"
    assert result.section_order == 4
