"""Tests for the split-out registry-audit helpers.

``registry_contract`` pins the four audit RULES. These tests pin the extracted
mechanics, so a refactor that makes constant resolution package-scoped again —
or that lets a second ``metadata["collection"]`` target go unreported — fails
here rather than showing up as a clean audit over a drifting registry.
"""

from __future__ import annotations

from rugbrain.ingest_sources import registry_contract, registry_facts, registry_scan


class _FakeModule:
    """Stands in for an imported source module (has __file__ and attributes)."""

    def __init__(self, file: str, **attrs: object) -> None:
        self.__file__ = file
        for key, value in attrs.items():
            setattr(self, key, value)


# ── constant resolution is MODULE-scoped ────────────────────────────────


def test_resolution_is_module_scoped_not_package_scoped(tmp_path) -> None:
    # The same name means two different things in two modules. Resolving
    # against the package would return defi_exploits for both and MISS a
    # second target declared as crime_reports.
    a = tmp_path / "a.py"
    a.write_text('DEFAULT_COLLECTION = "defi_exploits"\n', encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text('DEFAULT_COLLECTION = "crime_reports"\n', encoding="utf-8")
    mod_a = _FakeModule(str(a), DEFAULT_COLLECTION="defi_exploits")
    mod_b = _FakeModule(str(b), DEFAULT_COLLECTION="crime_reports")
    assert registry_scan.resolve(mod_a, "DEFAULT_COLLECTION") == "defi_exploits"
    assert registry_scan.resolve(mod_b, "DEFAULT_COLLECTION") == "crime_reports"


def test_module_const_falls_back_to_ast_for_unbound_names(tmp_path) -> None:
    path = tmp_path / "m.py"
    path.write_text('TARGET = "known_scams"\n', encoding="utf-8")
    mod = _FakeModule(str(path))  # TARGET deliberately not bound as an attribute
    assert registry_scan.module_const(mod, "TARGET") == "known_scams"


def test_module_const_returns_empty_for_unparsable_or_missing(tmp_path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def (:\n", encoding="utf-8")
    assert registry_scan.module_const(_FakeModule(str(broken)), "X") == ""
    assert registry_scan.module_const(_FakeModule(str(tmp_path / "nope.py")), "X") == ""
    assert registry_scan.read_text(str(tmp_path / "nope.py")) == ""
    assert registry_scan.read_text(None) == ""


def test_resolve_handles_literals_self_and_unknown(tmp_path) -> None:
    mod = _FakeModule(str(tmp_path / "m.py"), DEFAULT_COLLECTION="x")
    assert registry_scan.resolve(mod, "'crime_reports'") == "crime_reports"
    assert registry_scan.resolve(mod, "collection") == "x"
    assert registry_scan.resolve(mod, "") == ""
    assert registry_scan.resolve(mod, "SOMETHING_UNDECLARED") == ""


def test_resolve_will_not_read_a_property_as_a_value(tmp_path) -> None:
    # self.<property> is a runtime value we cannot see statically; returning
    # the module default would let a second target pass as clean.
    mod = _FakeModule(str(tmp_path / "m.py"), DEFAULT_COLLECTION="x")
    prop = property(lambda self: "hidden_target")
    mod.target = prop
    assert registry_scan.resolve(mod, "self.target") == ""


# ── write-primitive scan ────────────────────────────────────────────────


def test_writer_scan_flags_write_primitives_and_honours_the_allowlist(tmp_path) -> None:
    dirty = tmp_path / "dirty.py"
    dirty.write_text("qdrant_store.upsert(points)\n", encoding="utf-8")
    clean = tmp_path / "clean.py"
    clean.write_text("return [IngestDoc(...)]\n", encoding="utf-8")
    allowed = tmp_path / "telegram_web.py"
    allowed.write_text("qdrant_store.upsert(points)\n", encoding="utf-8")

    modules = {
        "dirty": _FakeModule(str(dirty)),
        "clean": _FakeModule(str(clean)),
        "telegram_web": _FakeModule(str(allowed)),
    }
    assert registry_scan.scan_writers(modules) == ["dirty"]


# ── collection-use scan catches the silent second target ────────────────


def test_collection_use_scan_reports_every_emitted_target(tmp_path) -> None:
    # The real sources name their targets as module constants (RUG_COLLECTION /
    # SCAM_COLLECTION), which is exactly why resolution has to read the module
    # rather than the package. Line 3 is the silent second target.
    src = tmp_path / "m.py"
    src.write_text(
        'RUG_COLLECTION = "rug_events"\n'
        'SCAM_COLLECTION = "known_scams"\n'
        'a = {"collection": RUG_COLLECTION}\n'
        'b = {"collection": SCAM_COLLECTION}\n',
        encoding="utf-8",
    )
    mod = _FakeModule(str(src), RUG_COLLECTION="rug_events", SCAM_COLLECTION="known_scams")
    uses = registry_scan.scan_collection_uses({"m": mod})
    assert [(u.resolved, u.line) for u in uses] == [("rug_events", 3), ("known_scams", 4)]


# ── the facts this module cannot own ────────────────────────────────────


def test_live_collections_rejects_a_non_http_scheme() -> None:
    # Never opens a socket for a URL we cannot validate: the registry must be
    # safe to run from any cron.
    assert registry_facts.live_collections("file:///etc/passwd") == set()


def test_live_collections_is_empty_when_unreachable() -> None:
    assert registry_facts.live_collections("http://127.0.0.1:1") == set()


def test_owned_collections_is_a_copy_not_the_shared_set() -> None:
    from rugbrain.qdrant_store.collections import _RMI_RAG_OWNED_COLLECTIONS

    owned = registry_facts.owned_collections()
    assert owned == set(_RMI_RAG_OWNED_COLLECTIONS)
    owned.add("mutation_probe")
    assert "mutation_probe" not in _RMI_RAG_OWNED_COLLECTIONS


# ── the public surface is unchanged ─────────────────────────────────────


def test_contract_reexports_its_documented_surface() -> None:
    for name in registry_contract.__all__:
        assert hasattr(registry_contract, name), f"lost {name}"
    assert registry_contract.CollectionUse is registry_scan.CollectionUse
    assert registry_contract.owned_collections is registry_facts.owned_collections
    assert registry_contract.live_collections is registry_facts.live_collections


def test_audit_still_runs_against_the_real_registry() -> None:
    rep = registry_contract.audit(qdrant_url=None)
    assert rep.sources > 0
    assert rep.owned, "the owned-set must resolve or every check is vacuous"
    # Known pre-existing finding: scythe_radar_datasets emits a second
    # collection. Pinned so a future refactor cannot quietly "fix" the audit
    # by dropping the branch detection.
    assert any("scythe_radar_datasets" in v for v in rep.violations)


def test_report_as_dict_is_json_serialisable() -> None:
    import json

    rep = registry_contract.audit(qdrant_url=None)
    assert json.loads(json.dumps(rep.as_dict(), sort_keys=True))["sources"] == rep.sources


def test_facts_module_does_not_read_source_text() -> None:
    # One job per module: the facts module must not reach for source text.
    # Resolving a collection target is registry_scan's job.
    src = (registry_facts.__file__ or "").rsplit("/", 1)[-1]
    assert src == "registry_facts.py"
    assert "scan_collection_uses" not in (registry_facts.__doc__ or "")
