"""Scope algebra (architecture §5): exhaustive over a representative domain, plus version ordering and the cue grammar."""
import itertools
from aegis.scope import (AnyDim, EntDim, UnknownDim, VerDim, combine, meet_dim, parse_cues, parse_version, rel_dim, scope_rel, Scope,
                         vkey, ver_between, ver_eq, ver_ge, ver_lt)

DIMS = {"ANY": AnyDim(), "UNK": UnknownDim(), "ge3.2": ver_ge("3.2"), "lt3.2": ver_lt("3.2"), "eq3.2.1": ver_eq("3.2.1"), "3.0-3.1": ver_between("3.0", "3.1"), "eq3.10": ver_eq("3.10")}

def test_version_order_is_not_float():
    assert vkey(parse_version("3.2")) < vkey(parse_version("3.2.1")) < vkey(parse_version("3.10"))
    assert vkey(parse_version("3.2")) == vkey(parse_version("3.2.0"))
    assert ver_ge("3.2").contains("3.2.1") and ver_ge("3.2").contains("3.10") and not ver_lt("3.2").contains("3.2")

def test_any_is_not_unknown():
    for k, d in DIMS.items():
        if k != "UNK": assert rel_dim(AnyDim(), d) == "OVERLAP"          # ANY x X -> OVERLAP
        assert rel_dim(UnknownDim(), d) == "UNKNOWN"                      # UNKNOWN x X -> UNKNOWN
    assert rel_dim(AnyDim(), UnknownDim()) == "UNKNOWN"

def test_rel_is_symmetric_exhaustive():
    for (ka, a), (kb, b) in itertools.product(DIMS.items(), repeat=2): assert rel_dim(a, b) == rel_dim(b, a), (ka, kb)

def test_concrete_overlap_and_disjoint():
    assert rel_dim(ver_ge("3.2"), ver_lt("3.2")) == "DISJOINT"
    assert rel_dim(ver_ge("3.2"), ver_eq("3.2.1")) == "OVERLAP"
    assert rel_dim(ver_lt("3.2"), ver_between("3.0", "3.1")) == "OVERLAP"
    assert rel_dim(ver_ge("3.2"), ver_between("3.0", "3.1")) == "DISJOINT"
    assert rel_dim(EntDim(ids=["a", "b"]), EntDim(ids=["b"])) == "OVERLAP" and rel_dim(EntDim(ids=["a"]), EntDim(ids=["b"])) == "DISJOINT"

def test_combine_rules():
    assert combine(["OVERLAP", "UNKNOWN", "DISJOINT"]) == "DISJOINT"       # a definite separation decides
    assert combine(["OVERLAP", "UNKNOWN"]) == "UNKNOWN" and combine(["OVERLAP", "OVERLAP"]) == "OVERLAP"

def test_scope_rel_widened_claim_conflicts_with_narrow():
    wide, narrow = Scope(), Scope(software_revision=ver_ge("3.2"))
    assert scope_rel(wide, narrow) == "OVERLAP"                              # 180 bar (ANY) vs 200 bar (>=3.2) would conflict
    unk = Scope(software_revision=UnknownDim())
    assert scope_rel(unk, narrow) == "UNKNOWN"

def test_meet():
    assert meet_dim(AnyDim(), ver_ge("3.2")) == ver_ge("3.2") and meet_dim(UnknownDim(), ver_ge("3.2")).kind == "unknown"
    assert meet_dim(ver_ge("3.2"), ver_lt("3.2")) is None
    assert meet_dim(ver_ge("3.0"), ver_lt("3.2")) == VerDim(lo="3.0", hi="3.2", lo_inc=True, hi_inc=False)

def test_cue_grammar():
    cases = {"On revisions prior to 3.2, normal discharge pressure was 180 bar": [ver_lt("3.2")],
             "Normal HPU discharge pressure is 200 bar on software revision 3.2 and later.": [ver_ge("3.2")],
             "Effective software revision 3.2, pressure sensor PS-04 is replaced": [ver_ge("3.2")],
             "Do not install PS-04A on a controller running firmware older than 3.2.": [ver_lt("3.2")],
             "Requires controller firmware >= 3.2": [ver_ge("3.2")],
             "Applies to software revisions 3.0 and 3.1 only.": [ver_between("3.0", "3.1")],
             "PS-04 on revisions prior to 3.2, PS-04A on revision 3.2 and later": [ver_lt("3.2"), ver_ge("3.2")],
             "The pump runs at 200 bar": []}
    for text, want in cases.items(): assert parse_cues(text) == want, text
