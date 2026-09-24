"""Contract for a redacted ContextCrypto → RugBrain public projection.

ContextCrypto owns the source corpus. This module accepts only a reviewed,
public-marked record and emits a minimal document for RugBrain's public
``public_crypto_patterns`` collection. It deliberately drops internal source
coordinates, prompts, model/provider metadata, and internal provenance. Code
snippets are allowed only for explicitly marked public/open-source records.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse

from rugbrain.knowledge_boundary import PUBLIC_COLLECTIONS

PUBLIC_PROJECTION_COLLECTION = "public_crypto_patterns"
PUBLIC_SOURCE_KIND = "contextcrypto_pattern"
PUBLIC_CODE_SOURCE_KIND = "contextcrypto_public_code"
_PUBLIC_SOURCE_KINDS = frozenset({PUBLIC_SOURCE_KIND, PUBLIC_CODE_SOURCE_KIND})
_ALLOWED_FIELDS = frozenset(
    {
        "category",
        "code_snippet",
        "description",
        "ecosystem",
        "explanation",
        "language",
        "library",
        "license",
        "published_at",
        "public_safe",
        "remediation",
        "severity",
        "source_name",
        "source_url",
        "tags",
        "title",
        "verdict",
    }
)
_REQUIRED_FIELDS = ("title", "description", "category", "verdict", "source_url", "license")
_ALLOWED_VERDICTS = frozenset({"clean", "defense", "weakness", "mixed", "unknown", "malicious"})
_INTERNAL_SOURCE_HOST = re.compile(
    r"(?i)(?:^|[.:-])(?:rugmunch|rmi|talos|cinnabox|netcup|internal)(?:[.:-]|$)|\.local$"
)
_SECRET_OR_INTERNAL = re.compile(
    r"(?i)(?:/srv/|/root/|/home/dev/|\b(?:qwen|deepinfra|deepseek|llama\.cpp|contextcrypto|"
    r"master_skills|dispatch_brief|rugmunch|rmi-[a-z]+|talos|netcup)\b|"
    r"\b(?:sk|pk|api|key)[-_][A-Za-z0-9_-]{12,}\b)"
)


class ProjectionError(ValueError):
    """Raised when a record is not safe to expose through RugBrain."""


def _clean_text(value: Any, field: str) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if _SECRET_OR_INTERNAL.search(text):
        raise ProjectionError(f"public projection contains internal material in {field}")
    return text


def _source_url(value: Any) -> str:
    text = _clean_text(value, "source_url")
    parsed = urlparse(text)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ProjectionError("source_url must be an external HTTP(S) URL")
    if _INTERNAL_SOURCE_HOST.search(parsed.hostname):
        raise ProjectionError("source_url must reference a public upstream project")
    return text


def project_contextcrypto_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal public document or fail closed.

    Input must be an explicitly reviewed projection record. Missing provenance
    or license metadata is an error; unknown fields are ignored rather than
    copied, so a future ContextCrypto payload cannot silently become public.
    """
    if not isinstance(record, dict):
        raise ProjectionError("projection record must be an object")
    if record.get("audience") != "public":
        raise ProjectionError("projection record must be explicitly marked audience=public")
    source_kind = record.get("source_kind")
    if source_kind not in _PUBLIC_SOURCE_KINDS:
        raise ProjectionError("projection record has an unsupported source_kind")
    if source_kind == PUBLIC_CODE_SOURCE_KIND:
        if record.get("public_safe") is not True:
            raise ProjectionError("public code records must set public_safe=true")
        for field in ("code_snippet", "language", "library"):
            if not record.get(field):
                raise ProjectionError(f"public code record missing {field}")

    missing = [field for field in _REQUIRED_FIELDS if not record.get(field)]
    if missing:
        raise ProjectionError(f"projection record missing required fields: {', '.join(missing)}")

    verdict = str(record["verdict"]).strip().lower()
    if verdict not in _ALLOWED_VERDICTS:
        raise ProjectionError("projection record has an unsupported verdict")

    public: dict[str, Any] = {}
    for field in _ALLOWED_FIELDS:
        if field not in record:
            continue
        value = record[field]
        if field in {"source_url"}:
            public[field] = _source_url(value)
        elif isinstance(value, str):
            cleaned = _clean_text(value, field)
            public[field] = cleaned[:6000] if field == "code_snippet" else cleaned
        elif field == "tags":
            if isinstance(value, list):
                public[field] = [_clean_text(tag, field) for tag in value]
        elif isinstance(value, (int, float, bool)):
            public[field] = value

    content_parts = [
        public.get("title", ""),
        public.get("description", ""),
        public.get("explanation", ""),
        public.get("remediation", ""),
    ]
    content = "\n\n".join(part for part in content_parts if part).strip()
    if source_kind == PUBLIC_CODE_SOURCE_KIND:
        code = str(public.get("code_snippet", "")).strip()
        language = str(public.get("language", "text")).strip()
        content = f"{content}\n\n```{language}\n{code}\n```"
    if not content:
        raise ProjectionError("projection record has no public explanatory content")
    if _SECRET_OR_INTERNAL.search(content):
        raise ProjectionError("public projection content failed leak scan")

    provenance = {
        "audience": "public",
        "source_kind": source_kind,
        "source_name": public.get("source_name", ""),
        "source_url": public["source_url"],
        "license": public["license"],
    }
    public["content"] = content
    public["audience"] = "public"
    public["source_kind"] = source_kind
    public["provenance"] = provenance
    public["content_hash"] = hashlib.sha256(
        json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return public


def projection_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Build metadata for ``QdrantVectorStore.insert`` from a projected record."""
    public = project_contextcrypto_record(record)
    return {
        "audience": "public",
        "source_kind": public.get("source_kind", PUBLIC_SOURCE_KIND),
        "category": public.get("category", ""),
        "verdict": public.get("verdict", "unknown"),
        "severity": public.get("severity", "medium"),
        "ecosystem": public.get("ecosystem", ""),
        "language": public.get("language", ""),
        "library": public.get("library", ""),
        "tags": public.get("tags", []),
        "source_url": public.get("source_url", ""),
        "license": public.get("license", ""),
        "content_hash": public["content_hash"],
        "collection": PUBLIC_PROJECTION_COLLECTION,
    }


def is_public_projection_collection(collection: str) -> bool:
    """Return whether *collection* is the approved projection target."""
    return collection in PUBLIC_COLLECTIONS and collection == PUBLIC_PROJECTION_COLLECTION


__all__ = [
    "PUBLIC_CODE_SOURCE_KIND",
    "PUBLIC_PROJECTION_COLLECTION",
    "PUBLIC_SOURCE_KIND",
    "ProjectionError",
    "is_public_projection_collection",
    "project_contextcrypto_record",
    "projection_metadata",
]
