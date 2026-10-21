"""Eval CSV round-trip, stratified selection, pair sampling, silver rubric (no DB, no LLM)."""

import csv
from dataclasses import fields

import pytest

from app.eval import pairs as pr
from app.eval import select as sel
from app.eval.files import LabelRow, PairRow, read_labels, read_pairs, write_labels, write_pairs

TEXT_COLUMNS = {"excerpt", "raw_text", "text", "summary", "evidence_quote"}


def test_committed_files_hold_no_source_text_columns():
    for cls in (LabelRow, PairRow):
        assert not {f.name for f in fields(cls)} & TEXT_COLUMNS


def test_label_csv_round_trip_keeps_types_and_unicode(tmp_path):
    rows = [
        LabelRow(
            id=7,
            title='Quotes "and", commas – ünïcode',
            source="S",
            date="2026-10-01",
            url="https://x.test/7",
            in_scope="y",
            stratum="hard_case",
            relevant="y",
            category="feature_update",
            impact="3",
            primary_entity="github",
            label_source="model:x silver_label.v1",
            label_reason="Because, reasons.",
        ),
        LabelRow(id=8, title="t", source="S", date="", url="u", in_scope="n", stratum="s"),
    ]
    path = tmp_path / "labels.csv"
    write_labels(rows, path)
    back = read_labels(path)
    assert back == rows and isinstance(back[0].id, int)
    assert back[0].is_labeled() and not back[1].is_labeled()
    assert not (tmp_path / "labels.csv.tmp").exists()  # atomic write cleaned up


def test_pair_csv_round_trip_and_column_check(tmp_path):
    p = PairRow(
        a_id=1,
        b_id=2,
        a_title="A",
        b_title="B",
        a_source="X",
        b_source="Y",
        a_date="2026-10-01",
        b_date="2026-10-02",
        similarity=0.8123,
        band="0.80-0.85",
        band_population=7,
        same_story="n",
    )
    path = tmp_path / "pairs.csv"
    write_pairs([p], path)
    (back,) = read_pairs(path)
    assert back == p and isinstance(back.similarity, float) and back.is_labeled()
    with path.open("w", newline="") as f:
        csv.writer(f).writerow(["a_id", "b_id"])
    with pytest.raises(ValueError, match="expected"):
        read_pairs(path)


def _cand(
    i,
    *,
    source="Blog",
    kind="official",
    in_scope=False,
    page_diff=False,
    relevant=None,
    category=None,
    title=None,
    prerelease=False,
):
    return sel.Candidate(
        id=i,
        title=title or f"Item {i}",
        source=source,
        kind=kind,
        url=f"u{i}",
        date="2026-10-01",
        in_scope=in_scope,
        page_diff=page_diff,
        prerelease=prerelease,
        relevant=relevant,
        category=category,
    )


def _catalog():
    cands = [
        _cand(i, in_scope=True, source="Changelog", relevant=True, category=c)
        for i, c in enumerate(
            ["feature_update", "feature_update", "product_launch", "security_incident"], start=100
        )
    ]
    cands += [
        _cand(i, in_scope=True, source="Press", kind="press", relevant=False)
        for i in range(200, 210)
    ]
    cands += [
        _cand(1053, in_scope=True, source="Sonatype Pricing", page_diff=True, relevant=True),
        _cand(1058, in_scope=True, source="GitHub Pricing", page_diff=True, relevant=True),
        _cand(50, in_scope=True, source="Sonatype Blog", relevant=True),
    ]
    titles = ["Acme acquires Foo", "Bar raises $20M Series B", "Partner news", "New pricing"]
    cands += [_cand(300 + n, source=f"Vendor {n % 3}", title=t) for n, t in enumerate(titles)]
    cands += [
        _cand(400 + n, source=f"Repo {n}", kind="github", title=f"org/tool v1.{n}.0")
        for n in range(3)
    ]
    cands += [
        _cand(500 + n, source=f"Repo {n}", kind="github", title=f"org/tool v1.0.{n + 1}")
        for n in range(3)
    ]
    cands += [_cand(600 + n, source=f"Blog {n % 20}") for n in range(60)]
    return cands


def test_selection_is_stratified_capped_and_deterministic():
    cands = _catalog()
    first = sel.select_eval_set(cands)
    assert first == sel.select_eval_set(cands)  # same seed → same set
    assert len(first) == sel.SIZE
    strata = {c.id: s for c, s in first}
    assert strata[50] == "hard_case" and strata[1053] == "hard_case:page_diff"
    assert strata[1058] == "page_diff"
    assert {s for s in strata.values() if s.startswith("in_scope:relevant")} >= {
        "in_scope:relevant:feature_update",
        "in_scope:relevant:product_launch",
        "in_scope:relevant:security_incident",
    }
    assert sum(s == "in_scope:irrelevant" for s in strata.values()) == 5
    assert strata[300] == "out_of_scope:hint:acquisition" and strata[301].endswith("funding")
    assert sum(s == "out_of_scope:release" for s in strata.values()) == 2
    assert sum(s == "out_of_scope:patch_release" for s in strata.values()) == 2
    out = [c for c, _ in first if not c.in_scope]
    per_source: dict[str, int] = {}
    for c in out:
        per_source[c.source] = per_source.get(c.source, 0) + 1
    assert max(per_source.values()) <= sel.MAX_PER_SOURCE
    assert sel.select_eval_set(cands, seed=9) != first


def test_patch_release_and_category_hints():
    assert sel.is_patch_release(_cand(1, title="SocketDev/socket-cli v1.0.2"))
    assert sel.is_patch_release(_cand(1, title="tool v2.0.0-rc1"))
    assert not sel.is_patch_release(_cand(1, title="docker/compose v5.6.0"))
    assert sel.is_patch_release(_cand(1, title="tool v5.6.0", prerelease=True))
    assert sel.category_hint("Snyk Secrets Now Generally Available") == "product_launch"
    assert sel.category_hint("Something else") is None


def test_bands_and_sampling_spread_cap_and_determinism():
    assert pr.band_of(0.60) == "0.60-0.65" and pr.band_of(0.9499) == "0.90-0.95"
    assert pr.band_of(0.95) is None and pr.band_of(0.59) is None
    pairs = [pr.Pair(i, 1000 + i, 0.61) for i in range(50)]  # crowded low band
    pairs += [pr.Pair(2000 + i, 3000 + i, 0.86) for i in range(2)]  # sparse high band
    pairs += [pr.Pair(5, 4000 + i, 0.72) for i in range(5)]  # one signal in many pairs
    picked = pr.sample(pairs, size=10)
    assert picked == pr.sample(pairs, size=10)
    bands = [b for _, b, _ in picked]
    assert bands.count("0.85-0.90") == 2  # every pair of a sparse band is taken
    # All five 0.70-0.75 pairs share signal 5: at most MAX_PER_SIGNAL of them are taken.
    assert bands.count("0.70-0.75") == pr.MAX_PER_SIGNAL
    used: dict[int, int] = {}
    for p, _, _ in picked:
        for i in (p.a_id, p.b_id):
            used[i] = used.get(i, 0) + 1
    assert max(used.values()) <= pr.MAX_PER_SIGNAL
    assert {n for _, b, n in picked if b == "0.60-0.65"} == {50}  # band population stored
    assert [p.similarity for p, _, _ in picked] == sorted(
        (p.similarity for p, _, _ in picked), reverse=True
    )
