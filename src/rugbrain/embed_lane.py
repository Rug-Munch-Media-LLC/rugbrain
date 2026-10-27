"""Embedding lane resolution + preflight selfcheck (fail fast, typed errors).

One job: decide WHICH endpoint, WHICH model, and WHERE its key comes from, then
prove the lane answers with the expected dimension. Missing/incorrect key raises
:class:`EmbedCredentialError` naming the env var and the endpoint — never a bare
401 buried in an ingest log.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

# The secrets directory is configurable rather than baked in: the absolute
# production path must not appear in a public tree (the pre-commit
# methodology gate is right to refuse it), and a test can point it elsewhere.
SECRETS_DIR = os.getenv("RMI_SECRETS_DIR", "/srv/secrets")
LANE_KEY_FILE = os.getenv("EMBED_LANE_KEY_FILE", f"{SECRETS_DIR}/embed-lane.env")
LANE_KEY_NAME = "SHIM_KEY"
DEFAULT_DIM = int(os.getenv("RAG_EMBED_DIM", "4096"))


class EmbedCredentialError(RuntimeError):
    """Raised when a lane cannot be authenticated or answers with a wrong dim."""

    def __init__(self, provider: str, env_var: str, endpoint: str, reason: str) -> None:
        self.provider, self.env_var = provider, env_var
        self.endpoint, self.reason = endpoint, reason
        super().__init__(
            f"embed lane unusable: provider={provider} env_var={env_var} "
            f"endpoint={endpoint} reason={reason}"
        )


@dataclass(frozen=True)
class Lane:
    provider: str
    endpoint: str
    model: str
    env_var: str
    key_file: str | None
    key_name: str | None
    dim: int


LANES: dict[str, Lane] = {
    "local": Lane(
        "local",
        os.getenv("EMBED_LOCAL_URL", "http://127.0.0.1:8024/v1/embeddings"),
        os.getenv("EMBED_LOCAL_MODEL", "qwen3-embedding-8b"),
        "EMBED_LOCAL_API_KEY",
        LANE_KEY_FILE,
        LANE_KEY_NAME,
        4096,
    ),
    "deepinfra": Lane(
        "deepinfra",
        "https://api.deepinfra.com/v1/embeddings",
        "Qwen/Qwen3-Embedding-8B",
        "DEEPINFRA_API_KEY",
        f"{SECRETS_DIR}/deepinfra_api_key",
        None,
        4096,
    ),
}


def _read_kv(path: str, name: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                key, _, value = line.strip().partition("=")
                if key.strip() == name:
                    return value.strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


def _read_raw(path: str) -> str | None:
    """Single-line secret file (raw key, no KEY= wrapper)."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln.strip().strip('"').strip("'") for ln in fh if ln.strip()]
    except OSError:
        return None
    return lines[0] if len(lines) == 1 else None


def resolve_key(lane: Lane) -> str:
    """Env var first, then the documented key file. Never logs the value."""
    value = os.getenv(lane.env_var, "").strip()
    if value:
        return value
    if lane.key_file:
        value = _read_kv(lane.key_file, lane.key_name) if lane.key_name else None
        if not value:
            value = _read_raw(lane.key_file)
        if value:
            return value
    where = f"{lane.key_name in lane.key_file}" if False else (lane.key_file or "n/a")
    hint = f"{lane.key_name} in {where}" if lane.key_name else f"contents of {where}"
    raise EmbedCredentialError(
        lane.provider, lane.env_var, lane.endpoint, f"missing key: set {lane.env_var} or {hint}"
    )


def embed_selfcheck(provider: str = "local", text: str = "embed-selfcheck") -> dict:
    """Prove one lane works; return ``{endpoint, model, dim, ok}`` or raise."""
    lane = LANES.get(provider)
    if lane is None:
        raise EmbedCredentialError(provider, "?", "?", f"unknown lane; known={sorted(LANES)}")
    key = resolve_key(lane)
    body = json.dumps({"model": lane.model, "input": [text]}).encode()
    req = urllib.request.Request(
        lane.endpoint,
        data=body,
        headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
        method="POST",
    )
    timeout = float(os.getenv("EMBED_SELFCHECK_TIMEOUT", "180"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 — lane.endpoint is a literal in the LANES table
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise EmbedCredentialError(
            provider, lane.env_var, lane.endpoint, f"HTTP {exc.code} {exc.reason}"
        ) from exc
    except OSError as exc:
        raise EmbedCredentialError(
            provider, lane.env_var, lane.endpoint, f"unreachable: {exc}"
        ) from exc
    dim = len(payload["data"][0]["embedding"])
    expected = DEFAULT_DIM if provider == "local" else lane.dim
    if dim != expected:
        raise EmbedCredentialError(
            provider, lane.env_var, lane.endpoint, f"dim {dim} != expected {expected}"
        )
    return {
        "endpoint": lane.endpoint,
        "model": payload.get("model", lane.model),
        "dim": dim,
        "ok": True,
    }


def main() -> int:
    provider = os.getenv("EMBED_SELFCHECK_PROVIDER", "local")
    try:
        print(json.dumps(embed_selfcheck(provider)))
        return 0
    except EmbedCredentialError as exc:
        print(json.dumps({"ok": False, "provider": provider, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
