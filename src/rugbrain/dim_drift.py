"""Dimension-drift gate: does the retired 4096-lane generation come back?

The operator decision (2026-09-29) is that qwen3-embedding-8b @ 4096 is the
only lane. 2560 (qwen3-embedding-4b) and 1024 (qwen3-embedding-0.6b) are
eliminated, not merely ignored. "Eliminated" has to mean *mechanically
enforceable*, because this project has already been bitten four separate ways:

  1. A 2560 default sat in ``embed_lane.py`` next to an already-8B model name,
     so the model said 8B and the dim said 2560. Whichever path read first won.
  2. DeepInfra's default embed model still answers **2560** with an HTTP 200
     (verified live 2026-09-27: Qwen3-Embedding-4B, 0.63s, dim 2560). A client
     that never states a dimension looks *perfectly healthy* and is wrong.
  3. A docstring asserted "every live collection is 2560-dim", which was false
     for 48 collections. The lie outlived the fact.
  4. An earlier version of *this* check read only one of Qdrant's two
     ``params.vectors`` shapes and reported healthy, populated 4096
     collections as having "no vector config".

So this module has two halves and they are deliberately independent:

  * :func:`scan_live` — GET-only probe of Qdrant. Every collection must be at
    the registry's canonical dim. No allowlist, no "expected" exceptions: a
    1024-dim collection is a bug, not a special case.
  * :func:`scan_static` — AST walk of the source tree for *executable*
    references to the retired generation.

THE CLASSIFICATION IS THE WHOLE PROBLEM
---------------------------------------
Over 100 files in this tree mention 2560. Almost all are prose describing a
generation that was retired. A check that flags those fires forever on
correct code and gets muted, which is worse than no check. A check that
ignores every ``2560`` mention misses a live default that will silently
produce wrong-width vectors. Both failure modes are real here, so every hit
is sorted into exactly one class, and the class is *derived from the
registry* rather than from a list of filenames:

  ``REGISTRY_DECLARATION``
      The retired value is being *named on purpose* so it can be recognised
      and refused. ``lane_table.PRESERVED_LANE_DIM``,
      ``lane_resolver.RETIRED_LANE_DIM``, ``dim_policy.MODEL_DIMS`` and the
      corpus manifest's ``observed``/``retired`` fields exist for no other
      reason. Deleting them would delete the ability to detect drift. Never a
      failure.

  ``MANIFEST_RECORD``
      A retired width recorded as a *measurement* — the corpus manifest's
      ``dim``/``observed``/``retired`` fields. Truthful bookkeeping, not a
      default. Never a failure.

  ``EXECUTABLE_DEFAULT``
      A retired model or width that a runtime path would actually send or fall
      back to: a ``os.getenv`` fallback a client transmits, a dim table entry,
      a vector-schema ``{"size": ...}``. **This is the failure.** It is the
      shape that produced the ``qdrant_insert_blocked requested_dim=2560
      configured_dims=[4096]`` incident and the DeepInfra 200-OK trap.

  ``MISLEADING_PROSE``
      A docstring or comment asserting the retired generation is current —
      "canonical", "native", "default", "the live corpus". Not a failure (a
      stale comment breaks nothing at runtime) but reported separately,
      because false PROSE is a slow poison: it misleads the next reader into
      reinstating exactly the default this gate forbids. Docstring hits are
      marked as such — a docstring that asserts something untrue is worse
      than a comment that is merely out of date, because a docstring is
      contract.

Nothing here is keyed to a filename. A new manifest module is covered by the
field-name rule; a new 4B default inside an existing manifest module is still
an ``EXECUTABLE_DEFAULT`` and still fails.
"""

from __future__ import annotations

import ast
import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from rugbrain.dim_policy import MODEL_DIMS, RETIRED_MODELS
from rugbrain.qdrant_store.collection_schema import dense_vector_dimensions
from rugbrain.qdrant_store.lane_resolver import CANONICAL_LANE_DIM, RETIRED_LANE_DIM
from rugbrain.qdrant_store.lane_table import PINNED_DIMS, PRESERVED_LANE_DIM

__all__ = [
    "CANONICAL_DIM",
    "LiveCheck",
    "StaticCheck",
    "Verification",
    "canonical_model",
    "default_qdrant_url",
    "retired_dims",
    "retired_models",
    "scan_live",
    "scan_static",
]

#: The one dim every collection must be at. Read from the registry — never
#: hardcoded — so a future generation flip moves this gate with it.
CANONICAL_DIM: Final[int] = int(CANONICAL_LANE_DIM)

#: Qdrant is production. This module issues GET and nothing else; there is no
#: delete path, no create path, and no write verb anywhere in this file.
_READ_ONLY: Final[str] = "GET"

DEFAULT_QDRANT_URL: Final[str] = "http://127.0.0.1:6334"
_REQUEST_TIMEOUT: Final[float] = 10.0

# ── Classification vocabularies ────────────────────────────────────────────
# These name the *kind of slot* a value sits in, never a particular file. They
# are deliberately narrow: widening one is how a check starts firing forever.

#: Assignment targets / module constants whose job is to hold an EMBEDDING
#: width. Deliberately narrow: "size" alone is excluded, because this tree has
#: ``5 * 1024 * 1024`` byte arithmetic in the backup paths, and
#: a chunk size is not a vector width. A bare ``size=`` on VectorParams is
#: caught instead by the vector-construction rule below, which is precise.
_DIM_NAME_TOKENS: Final[tuple[str, ...]] = (
    "dim",
    "dimension",
    "width",
    "vector",
    "embedding",
    "embed",
    "sparse",
    "dense",
)

#: Names that exist to *name the retired generation* on purpose. A retired
#: value bound to one of these is a registry declaration, by definition.
_REGISTRY_NAME_TOKENS: Final[tuple[str, ...]] = (
    "retired",
    "preserved",
    "legacy",
    "deprecated",
    "obsolete",
    "old_dim",
    "previous",
)

#: Manifest fields that record a *measurement* rather than choose a default.
_MANIFEST_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "dim",
        "observed",
        "observed_dim",
        "observed_live_dim",
        "retired",
        "retired_dims",
        "retired_dim",
        "expected_dim",
        "native_dim",
    }
)

#: Prose words that turn a retired mention into a claim about the CURRENT lane.
_STALE_PROSE_TOKENS: Final[tuple[str, ...]] = (
    "canonical",
    "current",
    "default",
    "native",
    "live corpus",
    "every live",
    "is the dim",
    "full-fidelity",
)


def default_qdrant_url() -> str:
    """Qdrant URL, env-overridable, defaulting to the local production port."""
    return os.getenv("QDRANT_URL", DEFAULT_QDRANT_URL).rstrip("/")


def canonical_model() -> str:
    """The registry's canonical embedding model name.

    Derived, not hardcoded: the canonical model is the one whose native dim
    *is* the canonical dim. This is why the check compares against the
    registry instead of against a literal copied out of it — a future flip
    changes the answer here and every comparison follows.
    """
    for model, dim in MODEL_DIMS.items():
        if dim == CANONICAL_DIM:
            return model
    return ""


def retired_dims() -> frozenset[int]:
    """Widths the registry itself has already marked as a dead generation.

    Derived from the registry, so eliminating a generation from the registry
    eliminates it from this gate. Three sources, all of them declarations that
    exist to *name* the dead lane:

      * ``lane_resolver.RETIRED_LANE_DIM``  — the retired canonical width
      * ``lane_table.PRESERVED_LANE_DIM``   — the retained read-only base
      * ``lane_table.PINNED_DIMS.values()`` — collections pinned below canonical

    2048 (the nemotron backups) is deliberately NOT in here. Those are live
    alternative providers, not a dead generation, and treating them as drift
    would be wrong.

    The canonical dim is subtracted: on a future host where the preserved base
    has been migrated up, a preserved width equal to canonical is not drift.
    """
    candidates = {
        int(RETIRED_LANE_DIM),
        int(PRESERVED_LANE_DIM),
        *(int(d) for d in PINNED_DIMS.values()),
    }
    return frozenset(d for d in candidates if d != CANONICAL_DIM)


def retired_models() -> Mapping[str, int]:
    """Retired embedding models and the width each one produced.

    A retired model name in a *default* position is the DeepInfra trap: the
    provider answers 200 and the vectors are the wrong width.

    Reads RETIRED_MODELS, not MODEL_DIMS. The 4B was moved out of MODEL_DIMS on
    2026-09-30 so callers could not resolve its dimension and recreate the
    retired lane; deriving the retired set from MODEL_DIMS then returned {} and
    this detector would have gone blind to exactly the case it exists to catch.
    """
    return {name: dim for name, (dim, _replacement) in RETIRED_MODELS.items()}


# ── Result types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LiveCheck:
    """Verdict of the live Qdrant half."""

    canonical_dim: int
    reachable: bool
    checked: int = 0
    offenders: tuple[tuple[str, tuple[int, ...], str], ...] = ()
    unparseable: tuple[tuple[str, str], ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.reachable and not self.offenders and not self.unparseable

    def to_dict(self) -> dict[str, Any]:
        return {
            "half": "live",
            "canonical_dim": self.canonical_dim,
            "reachable": self.reachable,
            "checked": self.checked,
            "offender_count": len(self.offenders),
            "offenders": [
                {"collection": name, "dims": list(dims), "reason": reason}
                for name, dims, reason in self.offenders
            ],
            "unparseable": [
                {"collection": name, "reason": reason} for name, reason in self.unparseable
            ],
            "error": self.error,
            "ok": self.ok,
        }


@dataclass(frozen=True, slots=True)
class StaticCheck:
    """Verdict of the static source-tree half, split by class.

    ``failures`` is the only thing that moves the exit code. ``prose`` is
    reported so a human can see it, and so a test can assert it is separated
    rather than silently merged into the pass.
    """

    scanned: int = 0
    failures: tuple[tuple[str, int, str, str], ...] = ()
    declarations: tuple[tuple[str, int, str, str], ...] = ()
    prose: tuple[tuple[str, int, str, str, bool], ...] = ()
    unparsed: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures and not self.unparsed

    def to_dict(self) -> dict[str, Any]:
        return {
            "half": "static",
            "scanned": self.scanned,
            "ok": self.ok,
            "failure_count": len(self.failures),
            "failures": [
                {"file": f, "line": ln, "class": cls, "detail": d}
                for f, ln, cls, d in self.failures
            ],
            "registry_declaration_count": len(self.declarations),
            "registry_declarations": [
                {"file": f, "line": ln, "class": cls, "detail": d}
                for f, ln, cls, d in self.declarations
            ],
            "misleading_prose_count": len(self.prose),
            "misleading_prose": [
                {"file": f, "line": ln, "class": cls, "detail": d, "is_docstring": is_doc}
                for f, ln, cls, d, is_doc in self.prose
            ],
            "unparsed": [{"file": f, "reason": r} for f, r in self.unparsed],
        }


@dataclass(frozen=True, slots=True)
class Verification:
    """Combined verdict. ``ok`` requires BOTH halves clean."""

    live: LiveCheck
    static: StaticCheck

    @property
    def ok(self) -> bool:
        return self.live.ok and self.static.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "canonical_dim": self.live.canonical_dim,
            "retired_dims": sorted(retired_dims()),
            "retired_models": dict(retired_models()),
            "canonical_model": canonical_model(),
            "live": self.live.to_dict(),
            "static": self.static.to_dict(),
        }

    def render(self) -> str:
        """Human-readable report. Offenders are named, never counted."""
        lines: list[str] = []
        retired = ", ".join(str(d) for d in sorted(retired_dims()))
        models = ", ".join(sorted(retired_models())) or "none"
        lines.append(
            f"canonical dim {self.live.canonical_dim} "
            f"(model {canonical_model() or 'unknown'}); retired widths: {retired}; "
            f"retired models: {models}"
        )

        live = self.live
        if not live.reachable:
            lines.append(f"LIVE      UNREACHABLE  {live.error}")
        elif live.ok:
            lines.append(f"LIVE      PASS  {live.checked} collections, all at {live.canonical_dim}")
        else:
            lines.append(
                f"LIVE      FAIL  {len(live.offenders)} of {live.checked} collections "
                f"off the {live.canonical_dim} lane "
                f"({live.canonical_dim - len(live.offenders)}/{live.checked} correct)"
            )
            for name, dims, reason in live.offenders:
                shown = ",".join(str(d) for d in dims) or "none"
                lines.append(f"            {name}: dim={shown} — {reason}")
            for name, reason in live.unparseable:
                lines.append(f"            {name}: {reason}")

        st = self.static
        if st.unparsed:
            lines.append(f"STATIC    UNPARSED  {len(st.unparsed)} file(s) did not parse")
            for name, reason in st.unparsed:
                lines.append(f"            {name}: {reason}")
        if st.failures:
            lines.append(
                f"STATIC    FAIL  {len(st.failures)} executable reference(s) to the "
                f"retired generation across {st.scanned} files"
            )
            for path, line, cls, detail in st.failures:
                lines.append(f"            {path}:{line}  [{cls}] {detail}")
        else:
            lines.append(
                f"STATIC    PASS  {st.scanned} files, no executable reference to the "
                f"retired generation ({len(st.declarations)} registry declaration(s) classified)"
            )
        if st.prose:
            docstrings = sum(1 for *_, is_doc in st.prose if is_doc)
            lines.append(
                f"PROSE     REPORT  {len(st.prose)} stale claim(s) about the retired "
                f"generation ({docstrings} in docstrings) — not a failure"
            )
            for path, line, _cls, detail, is_doc in st.prose:
                kind = "docstring" if is_doc else "comment"
                lines.append(f"            {path}:{line}  [{kind}] {detail}")

        lines.append(f"RESULT    {'PASS' if self.ok else 'FAIL'}")
        return "\n".join(lines)


# ── Live half ──────────────────────────────────────────────────────────────


def _get_json(url: str, timeout: float = _REQUEST_TIMEOUT) -> Any:
    """GET a URL and decode JSON. The only network verb this module uses."""
    request = urllib.request.Request(url, method=_READ_ONLY)  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _shape_of(vectors: Any) -> str:
    """Name the ``params.vectors`` shape Qdrant returned.

    Qdrant 1.x returns EITHER a bare single-vector object
    (``{"size": N, "distance": ...}``) OR a named-vector dict
    (``{"dense": {"size": N, "distance": ...}}``) when sparse vectors are
    present. Both shapes occur at 4096 on this host (90 bare, 6 named as of
    2026-09-29). Naming the shape is what stops the failure this check was
    written after: a parser that handles only one shape reports healthy
    populated collections as "no vector config".
    """
    if not isinstance(vectors, dict):
        return f"not-an-object:{type(vectors).__name__}"
    if isinstance(vectors.get("size"), int):
        return "bare"
    if any(isinstance(spec, dict) and "size" in spec for spec in vectors.values()):
        return "named"
    return "empty"


def scan_live(
    qdrant_url: str | None = None,
    *,
    timeout: float = _REQUEST_TIMEOUT,
    collections: Sequence[str] | None = None,
) -> LiveCheck:
    """GET-only census of every Qdrant collection's vector width.

    Any collection whose width is not :data:`CANONICAL_DIM` is a failure.
    There is no allowlist and no "expected" exception list: the whole point
    of the elimination is that every collection ends at the canonical dim,
    because the 2560 bases get deleted. A 1024-dim collection is a bug, not
    a special case, and a check that excuses it has not enforced anything.

    *collections* exists for the test's mutation harness, which injects a
    synthetic collection without touching production.
    """
    url = (qdrant_url or default_qdrant_url()).rstrip("/")
    try:
        if collections is None:
            listing = _get_json(f"{url}/collections", timeout)
            names = sorted(c["name"] for c in listing.get("result", {}).get("collections", []))
        else:
            names = sorted(collections)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError) as exc:
        return LiveCheck(
            canonical_dim=CANONICAL_DIM, reachable=False, error=f"{type(exc).__name__}: {exc}"
        )

    offenders: list[tuple[str, tuple[int, ...], str]] = []
    unparseable: list[tuple[str, str]] = []
    for name in names:
        try:
            result = _get_json(f"{url}/collections/{name}", timeout).get("result") or {}
        except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError) as exc:
            unparseable.append((name, f"probe failed: {type(exc).__name__}: {exc}"))
            continue
        params = (result.get("config") or {}).get("params") or {}
        vectors = params.get("vectors")
        shape = _shape_of(vectors)
        # Reuse the repo's own parser rather than re-reading the schema: this
        # is what guarantees the gate and the write path agree about a width.
        dims = tuple(sorted(dense_vector_dimensions(params)))
        if not dims:
            unparseable.append(
                (
                    name,
                    f"no dense vector config (params.vectors shape={shape}, "
                    f"raw={json.dumps(vectors)[:120]})",
                )
            )
            continue
        if dims != (CANONICAL_DIM,):
            offenders.append(
                (
                    name,
                    dims,
                    f"not the canonical {CANONICAL_DIM}-dim lane "
                    f"(shape={shape}, {result.get('points_count', 0)} points)",
                )
            )

    return LiveCheck(
        canonical_dim=CANONICAL_DIM,
        reachable=True,
        checked=len(names),
        offenders=tuple(offenders),
        unparseable=tuple(unparseable),
    )


# ── Static half ────────────────────────────────────────────────────────────


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids() of the Constant nodes that are module/class/function docstrings."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            found.add(id(body[0].value))
    return found


def _target_name(node: ast.AST) -> str:
    """The dotted/last name a value is being bound to, lowercased."""
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    if isinstance(node, ast.keyword) and node.arg:
        return node.arg.lower()
    return ""


def _dict_keys(tree: ast.AST) -> set[int]:
    """ids() of Constant nodes used as a DICT KEY.

    ``MODEL_DIMS = {"qwen3-embedding-4b": 2560}`` is a declaration: the key
    *names* the retired generation. That is the single most important
    exception in the whole classifier, because the canonical registry itself
    lives in exactly this shape and a check that flags it fires forever on
    correct code. Detecting it structurally (is this node a dict key?) keeps
    the rule honest for every future registry file, instead of exempting one
    filename.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                found.add(id(key))
    return found


def _model_ref_of(text: str, models: Mapping[str, int]) -> str | None:
    """Return the retired model this string IS, or None if it merely mentions one.

    A value is a model reference only when the whole string is the model name,
    or a ``provider/model`` path ending in it — the exact shape a client sends
    to an embeddings endpoint. A string that merely *contains* the name is not
    a reference: a HuggingFace cache path
    (``.../models--Qwen--Qwen3-Embedding-4B/snapshots/...``), a dataset card
    ("Training target: Qwen3-Embedding-4B fine-tuned"), and an argparse
    ``--help`` description all name the model without configuring anything.
    Flagging those would be a false positive on a check that must not fire
    forever, and they are handled on the prose track instead.
    """
    normalized = text.strip().lower()
    for name in models:
        low = name.lower()
        if normalized == low or normalized.endswith("/" + low):
            return name
    return None


def _is_env_default_slot() -> bool:
    """Deprecated shim retained for readability; env-defaults are indexed."""
    return False


_CALL_ENV: dict[int, bool] = {}


def _index_env_defaults(tree: ast.AST) -> None:
    """Record the ids of literals used as a configuration FALLBACK.

    Reset per file: the id() space is per-object, so a stale entry could only
    ever be a false negative, never a false positive — but clearing keeps the
    classifier honest when a file is scanned in isolation.

    Two shapes count as a fallback, and the second one is the important one:

    1. ``os.getenv("RAG_EMBED_DIM", "2560")`` — an explicit default argument.
    2. ``int(os.getenv("RAG_EMBED_DIM") or "2560")`` — a fallback ``or``-chain.

    Shape 2 is how the real defect in this repo was written::

        EMBED_DIM = int(os.getenv("QWEN_EMBED_DIMS") or os.getenv("RAG_EMBED_DIM") or "2560")

    with ``QWEN_EMBED_MODEL_NAME`` already set to 8B three lines above. An
    int-only scan misses it because the literal is a ``str``; a str-only scan
    that ignores the ``or`` chain misses it because the literal is not a
    ``getenv`` argument. Both are needed for the gate to see the exact bug the
    operator asked to make impossible.
    """
    _CALL_ENV.clear()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "getenv" and len(node.args) >= 2:
            for arg in node.args[1:]:
                for inner in ast.walk(arg):
                    if isinstance(inner, ast.Constant):
                        _CALL_ENV[id(inner)] = True
        # Fallback chain: every non-Call operand of an `or` is a default.
        if not isinstance(node.func, ast.Name) or node.func.id != "int":
            continue
        for arg in node.args:
            _mark_fallback_operands(arg)


def _mark_fallback_operands(node: ast.AST) -> None:
    """Mark literals in an ``or``-chain that are not themselves ``getenv`` calls."""
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        for value in node.values:
            if isinstance(value, ast.Call):
                continue  # the env lookup itself, not a fallback
            _mark_fallback_operands(value)
        return
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant):
            _CALL_ENV[id(inner)] = True


def _slot_of(node: ast.Constant, parent: ast.AST | None) -> tuple[str, str]:
    """Return ``(slot_kind, slot_name)`` for a constant's position.

    ``slot_kind`` is ``field`` (a keyword arg — a manifest record), ``name``
    (a module/class/function attribute name), or ``bare`` (an anonymous
    literal). The kind is what separates "recorded a measurement" from
    "chose a default".

    The parent may legitimately be a CALL rather than the assignment — in
    ``PRESERVED_LANE_DIM = int(os.getenv("X", "2560"))`` the literal's direct
    parent is the ``getenv`` call. Resolving only the direct parent would lose
    the binding name and misclassify a declaration as an anonymous default, so
    the caller walks the ancestor chain; this stays the single-step rule.
    """
    if isinstance(parent, ast.keyword):
        return "field", _target_name(parent)
    if isinstance(parent, ast.Assign | ast.AnnAssign | ast.AugAssign):
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        if targets:
            return "name", _target_name(targets[0])
    return "bare", ""


def _is_model_registry_value(node: ast.Constant, parents: Mapping[int, ast.AST]) -> bool:
    """True when the constant is a VALUE in a dict keyed by model names.

    ``MODEL_DIMS = {"qwen3-embedding-8b": 4096, "qwen3-embedding-4b": 2560}``
    — the retired model is the KEY and the retired width is the paired VALUE.
    That pairing is the declaration: it is how a registry says "this model
    produces this width". Detecting it structurally (are the sibling keys model
    names?) covers every registry file, present and future, instead of
    exempting ``dim_policy.py`` by name.

    A plain width table like ``PINNED_DIMS = {"wallet_clusters": 1024}`` has
    collection names as keys, not model names, so it is not matched here — and
    it does not need to be, because those values are already classified as
    manifest records by the field rule.
    """
    parent = parents.get(id(node))
    if not isinstance(parent, ast.Dict):
        return False
    for key in parent.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            lowered = key.value.strip().lower()
            if any(lowered == n.lower() or lowered.endswith("/" + n.lower()) for n in MODEL_DIMS):
                return True
    return False


def _vector_width_bindings(tree: ast.AST) -> set[str]:
    """Names bound to a Qdrant vector width anywhere in the file.

    Dataflow, not shape: this repo writes vector widths in two steps —

        size = vector_size or 2560
        client.create_collection(..., vectors_config=VectorParams(size=size, ...))

    — so the literal is nowhere near the constructor and no amount of
    structural proximity will connect them. The reliable signal is the
    *consuming call*: a value passed as ``size=`` to a vector-params
    constructor, or read out of a ``{"size": ...}`` literal, is a vector
    width, and whatever local name holds it is a width too. Collecting those
    names first turns a scattered two-line idiom into a precise rule, and is
    why dropping "size" from the name heuristics did not lose these.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if fname not in {"VectorParams", "SparseVectorParams", "DenseVectorParams"}:
            continue
        for keyword in node.keywords:
            if keyword.arg == "size" and isinstance(keyword.value, ast.Name):
                names.add(keyword.value.id.lower())
    return names


def _dict_size_bindings(tree: ast.AST) -> set[str]:
    """Names read out of a vector-schema dict literal's ``size`` key."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if (
                isinstance(key, ast.Constant)
                and key.value == "size"
                and isinstance(value, ast.Name)
            ):
                names.add(value.id.lower())
    return names


def _is_arithmetic_operand(node: ast.Constant, parents: Mapping[int, ast.AST]) -> bool:
    """True when the literal is a factor in arithmetic, not a standalone value.

    ``5 * 1024 * 1024`` and ``4 * 1024 * 1024`` are byte sizes in the backup
    paths. The literal is an *operand of a multiplication* — a
    unit conversion — and can never be an embedding width. Without this rule
    the gate reports four byte-size bugs that are not bugs, and a gate that
    cries wolf on a backup script is a gate that gets muted.
    """
    current: ast.AST | None = parents.get(id(node))
    for _ in range(3):
        if current is None:
            return False
        if isinstance(current, ast.BinOp):
            return True
        current = parents.get(id(current))
    return False


def _is_vector_construction(node: ast.Constant, parents: Mapping[int, ast.AST]) -> bool:
    """True when the literal is the width of a Qdrant vector schema.

    ``VectorParams(size=2560, ...)`` and ``{"size": 2560, "distance": "Cosine"}``
    both declare a collection's width directly, and the ``size`` key carries
    no embedding hint at all. Matching the constructor structurally is what
    lets "size" be dropped from the name heuristics without losing these —
    the two most dangerous widths in the tree, since they create the
    non-canonical collection the live half then refuses.
    """
    current: ast.AST | None = parents.get(id(node))
    for _ in range(3):
        if current is None:
            return False
        if isinstance(current, ast.Dict):
            for key, value in zip(current.keys, current.values, strict=False):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "size"
                    and not isinstance(value, (ast.Name, ast.Call, ast.Subscript))
                ):
                    return True
        if isinstance(current, ast.Call):
            func = current.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"VectorParams", "SparseVectorParams", "DenseVectorParams"}:
                return True
        current = parents.get(id(current))
    return False


def _resolve_slot(node: ast.Constant, parents: Mapping[int, ast.AST]) -> tuple[str, str]:
    """Nearest enclosing name/field for *node*, walking past call wrappers.

    ``PRESERVED_LANE_DIM = int(os.getenv("RAG_PRESERVED_LANE_DIM", "2560"))``
    has the literal two levels below the ``AnnAssign``. Stopping at the first
    ancestor that names anything keeps the registry's own declarations
    correctly classified, which is the difference between a check that can be
    trusted and one that has to be muted.
    """
    current: ast.AST | None = parents.get(id(node))
    for _ in range(4):  # bounded: a name is never more than a call or two away
        if current is None:
            break
        kind, name = _slot_of(node, current)
        if kind != "bare":
            return kind, name
        current = parents.get(id(current))
    return "bare", ""


def _is_dim_shaped(name: str) -> bool:
    return any(token in name for token in _DIM_NAME_TOKENS)


def _is_registry_named(name: str) -> bool:
    return any(token in name for token in _REGISTRY_NAME_TOKENS)


def _classification(
    *,
    slot_kind: str,
    slot_name: str,
    value_repr: str,
    retired: frozenset[int],
    models: Mapping[str, int],
    is_dict_key: bool = False,
    is_env_default: bool = False,
    is_vector_schema: bool = False,
    is_arithmetic: bool = False,
) -> str:
    """Sort one hit into exactly one class. This is the whole contract.

    Order matters and is deliberate:

    1. A dict KEY naming a retired model is a declaration — this is the shape
       the canonical registry itself uses (``MODEL_DIMS``), and exempting it by
       filename would be the exemption that rots.
    2. A registry-named binding (``PRESERVED_LANE_DIM``, ``retired_dims``) is
       a declaration no matter what it holds — that is what makes a
       declaration a declaration.
    3. A manifest field is a record, not a choice.
    4. A retired *model name* in any other real slot is an executable default.
       There is no legitimate "record which model we used" manifest field, and
       a model default is the DeepInfra 200-OK trap.
    5. A retired *width* only counts as a default when the slot is dim-shaped
       or it is an ``os.getenv`` fallback. This is what stops ``1024`` chunk
       sizes and batch limits — which appear all over the backup and
       observability code — from being reported as embedding drift.
    """
    if is_dict_key:
        return "REGISTRY_DECLARATION"
    if _is_registry_named(slot_name):
        return "REGISTRY_DECLARATION"
    if is_arithmetic:
        return "MANIFEST_RECORD"
    if slot_kind == "field" and slot_name in _MANIFEST_FIELD_NAMES:
        return "MANIFEST_RECORD"
    if _model_ref_of(value_repr.strip("'\""), models) is not None:
        return "EXECUTABLE_DEFAULT"
    if slot_kind == "field":
        # A retired width in a field we do not recognise as a manifest field:
        # only a default when the field is dim-shaped, it is an env fallback,
        # or it IS the width of a vector schema. The vector arm is load-bearing
        # for `VectorParams(size=2560)`, whose keyword is literally called
        # "size" and carries no embedding hint — the constructor is the only
        # evidence that it is a width.
        if _is_dim_shaped(slot_name) or is_env_default or is_vector_schema:
            return "EXECUTABLE_DEFAULT"
        return "MANIFEST_RECORD"
    if _is_dim_shaped(slot_name) or is_env_default or is_vector_schema:
        return "EXECUTABLE_DEFAULT"
    return "MANIFEST_RECORD"


def _prose_claim(text: str, retired: frozenset[int], models: Mapping[str, int]) -> str | None:
    """Describe a stale prose claim about the retired generation, or None.

    False PROSE is not a failure — a stale comment breaks nothing at runtime.
    A docstring that asserts something untrue is worse, because a docstring
    is contract, so both are reported but flagged by kind.
    """
    low = text.lower()
    hits_retired = any(str(d) in low for d in retired) or any(name in low for name in models)
    if not hits_retired:
        return None
    claims = [token for token in _STALE_PROSE_TOKENS if token in low]
    if not claims:
        return None
    marker = ", ".join(repr(c) for c in claims[:3])
    return f"asserts the retired generation is current ({marker}); text is false prose"


def _iter_python_files(roots: Sequence[Path]) -> Iterator[Path]:
    """Yield every .py under each root.

    A root may be a single FILE or a directory. A file root is the case that
    matters for the mutation tests — and it is the case that silently made
    every "scan just this file" probe return zero files, because
    ``Path("x.py").rglob("*.py")`` is empty. A gate that reports "clean"
    because it looked at nothing is worse than no gate, so the branch is
    explicit.
    """
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix == ".py":
                yield root
            continue
        yield from sorted(root.rglob("*.py"))


def scan_static(
    roots: Sequence[Path | str] | None = None,
    *,
    skip: Sequence[Path | str] = (),
) -> StaticCheck:
    """AST-walk the tree for executable references to the retired generation.

    Parsing rather than grepping is the load-bearing choice: a plain text scan
    cannot tell a comment from a default, and this tree has 100+ files that
    mention 2560 in prose while only a handful carry a live default. Docstrings
    and comments are peeled off and reported on their own track.
    """
    base = Path(__file__).resolve().parents[2]
    if roots is None:
        roots = [base / "src", base / "scripts"]
    root_paths = [Path(r) for r in roots]
    skip_paths = {Path(s).resolve() for s in skip}

    retired = retired_dims()
    models = retired_models()

    failures: list[tuple[str, int, str, str]] = []
    declarations: list[tuple[str, int, str, str]] = []
    prose: list[tuple[str, int, str, str, bool]] = []
    unparsed: list[tuple[str, str]] = []
    scanned = 0

    for path in _iter_python_files(root_paths):
        resolved = path.resolve()
        if resolved in skip_paths or "__pycache__" in resolved.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            unparsed.append((str(path), f"{type(exc).__name__}: {exc}"))
            continue
        scanned += 1
        rel = _relative(path, base)

        # Comments are invisible to the AST, so they are recovered from source
        # lines and checked separately. They can never fail the gate.
        for lineno, text in _iter_comments(source):
            claim = _prose_claim(text, retired, models)
            if claim:
                prose.append((rel, lineno, "MISLEADING_PROSE", claim, False))

        docstrings = _docstring_nodes(tree)
        dict_keys = _dict_keys(tree)
        _index_env_defaults(tree)
        width_names = _vector_width_bindings(tree) | _dict_size_bindings(tree)
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            value = node.value
            is_key = id(node) in dict_keys
            is_env = id(node) in _CALL_ENV
            if isinstance(value, str):
                if id(node) in docstrings:
                    claim = _prose_claim(value, retired, models)
                    if claim:
                        prose.append((rel, node.lineno, "MISLEADING_PROSE", claim, True))
                continue
            if not isinstance(value, int) or isinstance(value, bool):
                continue
            if value not in retired:
                continue
            kind, slot = _resolve_slot(node, parents)
            cls = _classification(
                slot_kind=kind,
                slot_name=slot,
                value_repr=repr(value),
                retired=retired,
                models=models,
                is_dict_key=is_key or _is_model_registry_value(node, parents),
                is_env_default=is_env,
                is_vector_schema=_is_vector_construction(node, parents) or slot in width_names,
                is_arithmetic=_is_arithmetic_operand(node, parents),
            )
            where = "env-default" if is_env else f"{kind} {slot or '<anonymous>'!r}"
            record = (rel, node.lineno, cls, f"retired width {value} in {where}")
            if cls == "EXECUTABLE_DEFAULT":
                failures.append(record)
            else:
                declarations.append(record)

        # Retired WIDTH written as a STRING, e.g.
        #     EMBED_DIM = int(os.getenv("RAG_EMBED_DIM") or "2560")
        # This is not a stylistic detail, it is the exact defect the operator
        # asked to make impossible: `embed_lane.py` carried an 8B *model*
        # default beside a 2560 *dim* default, so the model said one thing and
        # the width another and whichever was read first won. An int-only scan
        # misses it completely, because the literal is a str. Only counted
        # when the slot is dim-shaped or it is an env fallback, so a prose
        # string that happens to contain "2560" is not a failure.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value.strip() not in {str(d) for d in retired}:
                continue
            if id(node) in docstrings or id(node) in dict_keys:
                continue
            is_env = id(node) in _CALL_ENV
            kind, slot = _resolve_slot(node, parents)
            # A registry-named binding stays a declaration in this pass too.
            # `PRESERVED_LANE_DIM = int(os.getenv(..., "2560"))` is env-gated
            # AND registry-named; without this the check fires forever on the
            # single line whose whole job is to name the retired width.
            if _is_registry_named(slot):
                declarations.append(
                    (
                        rel,
                        node.lineno,
                        "REGISTRY_DECLARATION",
                        f"retired width {node.value.strip()} as a string in {kind} {slot!r}",
                    )
                )
                continue
            if not (is_env or _is_dim_shaped(slot) or slot in width_names):
                continue
            where = "env-default" if is_env else f"{kind} {slot or '<anonymous>'!r}"
            failures.append(
                (
                    rel,
                    node.lineno,
                    "EXECUTABLE_DEFAULT",
                    f"retired width {node.value.strip()} as a string in {where}",
                )
            )

        # Retired MODEL names in string constants, same slot classification.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if _model_ref_of(node.value, models) is None:
                continue
            is_doc = id(node) in docstrings
            is_key = id(node) in dict_keys
            is_env = id(node) in _CALL_ENV
            if is_doc:
                prose.append(
                    (
                        rel,
                        node.lineno,
                        "MISLEADING_PROSE",
                        "docstring names a retired model as if current",
                        True,
                    )
                )
                continue
            if is_key:
                declarations.append(
                    (
                        rel,
                        node.lineno,
                        "REGISTRY_DECLARATION",
                        f"dict key names the retired model {node.value.strip()[:60]!r}",
                    )
                )
                continue
            kind, slot = _resolve_slot(node, parents)
            cls = _classification(
                slot_kind=kind,
                slot_name=slot,
                value_repr=node.value,
                retired=retired,
                models=models,
                is_env_default=is_env,
            )
            where = "env-default" if is_env else f"{kind} {slot or '<anonymous>'!r}"
            record = (
                rel,
                node.lineno,
                cls,
                f"retired model {node.value.strip()[:60]!r} in {where}",
            )
            if cls == "EXECUTABLE_DEFAULT":
                failures.append(record)
            else:
                declarations.append(record)

    return StaticCheck(
        scanned=scanned,
        failures=tuple(failures),
        declarations=tuple(declarations),
        prose=tuple(prose),
        unparsed=tuple(unparsed),
    )


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base))
    except ValueError:
        return str(path)


def _iter_comments(source: str) -> Iterator[tuple[int, str]]:
    """Yield ``(lineno, text)`` for every comment line, tokenise-safely.

    ``tokenize`` rather than a ``#`` split so a ``#`` inside a string is not
    mistaken for a comment and a 2560 inside a URL is not a claim.
    """
    import io
    import tokenize

    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                yield token.start[0], token.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return


def verify(
    *,
    qdrant_url: str | None = None,
    roots: Sequence[Path | str] | None = None,
    skip: Sequence[Path | str] = (),
    timeout: float = _REQUEST_TIMEOUT,
    collections: Sequence[str] | None = None,
) -> Verification:
    """Run both halves. ``ok`` is true only when both are clean."""
    return Verification(
        live=scan_live(qdrant_url, timeout=timeout, collections=collections),
        static=scan_static(roots, skip=skip),
    )
