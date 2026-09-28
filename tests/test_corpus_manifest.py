"""The declaration layer REFUSES what it should and ACCEPTS what it should.

Every assertion here encodes the operator's ask — "categorize before embed so
there is no confusion sorting nonsense" and "never again a second of dim
confusion" — as a property of code rather than a convention someone follows.

The required cases, this half:
    1. an undeclared corpus is REFUSED
    2. a declared corpus is ACCEPTED
    3. the axes are CLOSED vocabularies, and licences stay recorded

The live-width and twin-delegation halves live in ``test_corpus_manifest_verify``.
Split for the 200-line cap; the fixtures are shared in ``corpus_manifest_fixtures``.
"""

from __future__ import annotations

import pytest

from tests.corpus_manifest_fixtures import CorpusDomain, CorpusLanguage, decl
from rugbrain.corpus import (
    DECLARED,
    UndeclaredCorpus,
    require_declared,
)
from rugbrain.corpus.schema import CODE_LANGUAGES
from rugbrain.qdrant_store.lane_table import CANONICAL_LANE_DIM, PRESERVED_LANE_DIM


# --------------------------------------------------------------------------
# 1. UNDECLARED IS REFUSED
# --------------------------------------------------------------------------
class TestRefusal:
    def test_unknown_key_is_refused(self):
        """A name absent from the manifest never reaches the embedder."""
        with pytest.raises(UndeclaredCorpus, match="absent_from_manifest"):
            require_declared("totally-unknown-corpus")

    def test_missing_dim_is_refused(self):
        """No declared dim is the whole point: fail closed, not warn.

        This is the case the operator named. A corpus with a name, a language and
        a collection but no dim is refused, because a dim is the one thing that
        cannot be invented at embed time.
        """
        with pytest.raises(UndeclaredCorpus, match="incomplete_declaration"):
            require_declared(decl(dim=None))

    def test_missing_source_url_is_refused(self):
        """Provenance is part of the declaration, not decoration."""
        with pytest.raises(UndeclaredCorpus, match="incomplete_declaration"):
            require_declared(decl(source_url=""))

    def test_missing_licence_is_refused(self):
        """Every entry records a licence, so the product-bound day can find
        the ones that were never cleared. An absent one is not that."""
        with pytest.raises(UndeclaredCorpus, match="incomplete_declaration"):
            require_declared(decl(licence=""))

    def test_code_corpus_with_unclassified_language_is_refused(self):
        """The categorisation failure the ask names: a code corpus with no language."""
        with pytest.raises(UndeclaredCorpus, match="code_corpus_needs_a_real_language"):
            require_declared(decl(language=CorpusLanguage.UNCLASSIFIED))

    def test_domain_without_language_is_refused(self):
        with pytest.raises(UndeclaredCorpus, match="domain_set_but_language_unclassified"):
            require_declared(decl(domain=CorpusDomain.SCAM, language=CorpusLanguage.UNCLASSIFIED))

    def test_retired_lane_is_refused_for_write(self):
        """A 2560 dim is readable but is never a write target.

        The gate reuses ``lane_table`` rather than repeating the number: 2560 is
        the retired generation, and the canonical lane is 4096.
        """
        with pytest.raises(UndeclaredCorpus, match="retired_lane_is_not_a_write_target"):
            require_declared(decl(dim=PRESERVED_LANE_DIM))

    def test_retired_lane_is_allowed_for_read(self):
        """Reading a 2560 collection is legitimate; only writing is refused."""
        d = decl(dim=PRESERVED_LANE_DIM)
        assert require_declared(d, for_write=False) is d

    def test_refusal_is_not_a_warning(self):
        """No warning path exists. The exception type is the contract."""
        with pytest.raises(UndeclaredCorpus):
            require_declared(decl(dim=None))
        assert issubclass(UndeclaredCorpus, RuntimeError)

    def test_gate_has_no_env_override(self):
        """A gate that an env var can soften is a convention, not a gate."""
        import inspect

        from rugbrain.corpus import gate

        src = inspect.getsource(gate)
        assert "getenv" not in src and "environ" not in src, "gate must not read env"


# --------------------------------------------------------------------------
# 2. DECLARED IS ACCEPTED
# --------------------------------------------------------------------------
class TestAcceptance:
    def test_complete_declaration_passes(self):
        d = decl()
        assert require_declared(d) is d

    @pytest.mark.parametrize(
        "key",
        ["codebase_reference_v2", "threat_intel_v2", "known_scams", "defi_exploits", "rug_events"],
    )
    def test_every_real_corpus_passes_its_own_gate(self, key: str):
        """A manifest that could not embed its own corpora would be theatre."""
        assert require_declared(key).corpus_key == key

    def test_manifest_is_internally_consistent(self):
        """Ids unique; every entry complete; every declared dim canonical."""
        ids = [d.corpus_key for d in DECLARED]
        assert len(ids) == len(set(ids)), "duplicate corpus id"
        for d in DECLARED:
            assert d.is_fully_declared, f"incomplete declaration: {d.corpus_key}"
            assert d.dim == CANONICAL_LANE_DIM, f"non-canonical dim: {d.corpus_key}"

    def test_every_axis_is_from_a_closed_vocabulary(self):
        """No free text on language or domain — that is what makes an
        uncategorised corpus undeclinable."""
        for d in DECLARED:
            assert isinstance(d.language, CorpusLanguage)
            assert isinstance(d.domain, CorpusDomain)

    def test_code_corpus_language_matches_the_measured_distribution(self):
        """The code corpus is NOT Solidity-dominant, and the manifest says so.

        The belief that this fleet is a Solidity corpus survived because nothing
        recorded which axis a repo belonged to. rust 24.38% / go 17.44% /
        typescript 9.86% / solidity 7.78% / python 5.24% is the measured split
        (audit §2).
        """
        code = [d for d in DECLARED if d.domain is CorpusDomain.CODE]
        assert code
        assert all(d.language in CODE_LANGUAGES for d in code)
        main = next(d for d in code if d.collection == "codebase_reference_v2")
        assert main.language is CorpusLanguage.RUST

    def test_declared_dim_and_live_width_are_separate_facts(self):
        """dim is the WRITE lane; observed_live_dim is what Qdrant has.

        Conflating them is how a 2560/4096 split gets discovered by the dimension
        guard instead of by a manifest. ``threat_intel_v2`` is the worked case:
        measured 2560, declares the 4096 write lane, and its twin holds those
        points — a fact, not a contradiction.
        """
        base = next(d for d in DECLARED if d.collection == "threat_intel_v2")
        assert base.dim == CANONICAL_LANE_DIM
        assert base.observed_live_dim == PRESERVED_LANE_DIM
        assert PRESERVED_LANE_DIM in base.retired_dims

    def test_measured_live_widths_agree_with_qdrant(self):
        """The recorded widths are MEASURED, not assumed.

        Every declaration that names a live width was stamped from Qdrant on
        MEASURED_ON. This asserts the invariant that keeps the manifest honest:
        a declaration whose live width differs from its declared write lane must
        say so in ``retired_dims`` only when a twin actually exists to hold the
        canonical points.
        """
        from rugbrain.qdrant_store import lane_resolver as lr

        for d in DECLARED:
            if d.observed_live_dim in (None, d.dim):
                continue
            # "changed" means it has a real twin; unchanged means it is stranded.
            has_twin = lr.resolve_collection(d.collection) != d.collection
            if has_twin:
                assert d.observed_live_dim in d.retired_dims, d.corpus_key
            else:
                assert d.observed_live_dim not in d.retired_dims, d.corpus_key


# --------------------------------------------------------------------------
# 3. LICENCE: usable internally, flagged for a product day
# --------------------------------------------------------------------------
class TestLicence:
    def test_every_declaration_records_a_licence(self):
        for d in DECLARED:
            assert d.licence, f"no licence recorded: {d.corpus_key}"

    def test_unlicensed_and_copyleft_are_usable_but_not_product_cleared(self):
        """The internal/private ruling: the test is redistribution, not use.

        No-licence and copyleft corpora are usable here because nothing ships.
        They must still read as NOT cleared for a product, so a product-bound day
        can identify them.
        """
        flagged = [d for d in DECLARED if d.licence in ("unlicensed", "noassertion", "copyleft")]
        assert flagged, "expected the ruling to keep some non-product-cleared flags"
        for d in flagged:
            assert not d.declared_for_redistribution(), d.corpus_key
            assert str(d.licence_status) == "internal-only"
