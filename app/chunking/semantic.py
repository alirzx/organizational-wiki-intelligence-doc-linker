from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from app.core.config import Settings
from app.core.ids import sha256_text, stable_point_id
from app.models.domain import CanonicalDocument, CanonicalSection, ChunkRecord
from app.models.embedding import EmbeddingService

SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?؟؛])\s+|\n+")


@dataclass(frozen=True)
class Unit:
    text: str
    tokens: int


class SemanticChunker:
    def __init__(self, settings: Settings, embedder: EmbeddingService):
        self.settings = settings
        self.embedder = embedder

    def _split_units(self, text: str) -> list[Unit]:
        paragraphs = [item.strip() for item in re.split(r"\n{2,}", text) if item.strip()]
        raw_units: list[str] = []
        for paragraph in paragraphs or [text]:
            tokens = self.embedder.count_tokens(paragraph)
            if tokens <= self.settings.chunk_target_tokens:
                raw_units.append(paragraph)
                continue
            pieces = [item.strip() for item in SENTENCE_BOUNDARY.split(paragraph) if item.strip()]
            raw_units.extend(pieces or [paragraph])
        units: list[Unit] = []
        for raw in raw_units:
            count = self.embedder.count_tokens(raw)
            if count <= self.settings.chunk_max_tokens:
                units.append(Unit(raw, count))
                continue
            units.extend(self._hard_split(raw))
        return units

    def _hard_split(self, text: str) -> list[Unit]:
        words = text.split()
        if not words:
            return []
        result: list[Unit] = []
        buffer: list[str] = []
        for word in words:
            candidate = " ".join(buffer + [word])
            if buffer and self.embedder.count_tokens(candidate) > self.settings.chunk_max_tokens:
                joined = " ".join(buffer)
                result.append(Unit(joined, self.embedder.count_tokens(joined)))
                buffer = [word]
            else:
                buffer.append(word)
        if buffer:
            joined = " ".join(buffer)
            result.append(Unit(joined, self.embedder.count_tokens(joined)))
        return result

    @staticmethod
    def _cosine(left: np.ndarray, right: np.ndarray) -> float:
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        return float(np.dot(left, right) / denominator) if denominator else 0.0

    def _semantic_threshold(self, units: list[Unit]) -> list[float]:
        if len(units) < 2:
            return []
        embeddings = self.embedder.encode_documents([unit.text for unit in units]).dense
        vectors = [np.asarray(item, dtype=np.float32) for item in embeddings]
        return [self._cosine(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]

    def _group_units(self, units: list[Unit]) -> list[list[Unit]]:
        if not units:
            return []
        similarities = self._semantic_threshold(units)
        threshold = (
            float(np.percentile(similarities, self.settings.semantic_break_percentile))
            if similarities
            else -1.0
        )
        groups: list[list[Unit]] = []
        current: list[Unit] = []
        current_tokens = 0
        for index, unit in enumerate(units):
            if current and current_tokens + unit.tokens > self.settings.chunk_max_tokens:
                groups.append(current)
                current = self._overlap_tail(current)
                current_tokens = sum(item.tokens for item in current)
            current.append(unit)
            current_tokens += unit.tokens
            next_similarity = similarities[index] if index < len(similarities) else 1.0
            semantic_break = (
                current_tokens >= self.settings.chunk_min_tokens
                and next_similarity <= threshold
                and current_tokens >= self.settings.chunk_target_tokens // 2
            )
            target_break = current_tokens >= self.settings.chunk_target_tokens
            if semantic_break or target_break:
                groups.append(current)
                current = self._overlap_tail(current)
                current_tokens = sum(item.tokens for item in current)
        if current:
            if groups and sum(item.tokens for item in current) < self.settings.chunk_min_tokens:
                merged = groups.pop() + current
                if sum(item.tokens for item in merged) <= self.settings.chunk_max_tokens:
                    groups.append(merged)
                else:
                    groups.append(current)
            else:
                groups.append(current)
        # Remove duplicate overlap-only groups that can occur at exact boundaries.
        deduped: list[list[Unit]] = []
        seen: set[str] = set()
        for group in groups:
            text = "\n\n".join(item.text for item in group).strip()
            digest = sha256_text(text)
            if text and digest not in seen:
                seen.add(digest)
                deduped.append(group)
        return deduped

    def _overlap_tail(self, units: list[Unit]) -> list[Unit]:
        budget = self.settings.chunk_overlap_tokens
        if budget <= 0:
            return []
        tail: list[Unit] = []
        total = 0
        for unit in reversed(units):
            if tail and total + unit.tokens > budget:
                break
            tail.append(unit)
            total += unit.tokens
            if total >= budget:
                break
        return list(reversed(tail))

    def _section_chunks(self, section: CanonicalSection) -> list[tuple[str, int]]:
        total = self.embedder.count_tokens(section.text)
        if total <= self.settings.chunk_max_tokens:
            return [(section.text, total)]
        units = self._split_units(section.text)
        groups = self._group_units(units)
        return [
            (text, self.embedder.count_tokens(text))
            for group in groups
            if (text := "\n\n".join(item.text for item in group).strip())
        ]

    def chunk(self, document: CanonicalDocument) -> list[ChunkRecord]:
        output: list[ChunkRecord] = []
        for section in document.sections:
            section_chunks = self._section_chunks(section)
            for chunk_index, (text, token_count) in enumerate(section_chunks):
                content_hash = sha256_text(text)
                point_id = stable_point_id(
                    document.organization_id, document.wiki_id, document.document_id,
                    document.document_version, section.section_id, chunk_index, content_hash
                )
                output.append(ChunkRecord(
                    point_id=point_id, document_id=document.document_id,
                    organization_id=document.organization_id, wiki_id=document.wiki_id,
                    document_version=document.document_version, document_title=document.title,
                    source_filename=document.source_filename, language=document.language,
                    tags=document.tags, visibility=document.visibility, acl_groups=document.acl_groups,
                    artifact_bucket=document.artifact_bucket, artifact_key=document.artifact_key,
                    artifact_sha256=document.artifact_sha256, artifact_etag=document.artifact_etag,
                    stage2_job_id=document.stage2_job_id,
                    stage2_pipeline_version=document.stage2_pipeline_version, section_id=section.section_id,
                    section_type_id=section.type_id, section_title=section.title, section_order=section.order,
                    page_start=section.page_start, page_end=section.page_end, source_blocks=section.source_blocks,
                    section_confidence=section.confidence, section_classifier=section.classifier,
                    chunk_index=chunk_index, text=text, content_hash=content_hash, token_count=token_count,
                    document_metadata=document.metadata,
                ))
        return output
