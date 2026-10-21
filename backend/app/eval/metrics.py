"""Metric calculations for the eval report (pure functions, no DB, no LLM).

The reference is the silver label (a stronger model), so every number here is AGREEMENT with that
model, not accuracy against human ground truth.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


def ratio(n: int | float, d: int | float) -> float | None:
    return round(n / d, 3) if d else None


# ---------- triage ----------


def binary(pred: Sequence[bool], ref: Sequence[bool]) -> dict[str, Any]:
    """Agreement plus precision / recall / F1 of `pred` against `ref` (positive = True)."""
    tp = sum(p and r for p, r in zip(pred, ref, strict=True))
    fp = sum(p and not r for p, r in zip(pred, ref, strict=True))
    fn = sum(not p and r for p, r in zip(pred, ref, strict=True))
    tn = sum(not p and not r for p, r in zip(pred, ref, strict=True))
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)
    f1 = round(2 * precision * recall / (precision + recall), 3) if precision and recall else None
    return {
        "n": len(pred),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "agreement": ratio(tp + tn, len(pred)),
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


# ---------- category / impact / entity ----------


def category(pairs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """pairs = (pipeline, silver). Accuracy and the mismatches as 'silver → pipeline' counts."""
    hits = sum(p == s for p, s in pairs)
    confusion = Counter(f"{s} → {p}" for p, s in pairs if p != s)
    return {
        "n": len(pairs),
        "agreement": ratio(hits, len(pairs)),
        "confusion": dict(confusion.most_common()),
    }


def impact(pairs: Sequence[tuple[int, int]]) -> dict[str, Any]:
    """pairs = (pipeline, silver). Signed error = pipeline − silver (> 0: pipeline over-rates)."""
    n = len(pairs)
    errors = [p - s for p, s in pairs]
    return {
        "n": n,
        "exact": ratio(sum(e == 0 for e in errors), n),
        "within_1": ratio(sum(abs(e) <= 1 for e in errors), n),
        "mean_signed_error": round(sum(errors) / n, 3) if n else None,
        "mean_abs_error": round(sum(abs(e) for e in errors) / n, 3) if n else None,
        "errors": dict(sorted(Counter(errors).items())),
    }


def entity(rows: Sequence[tuple[Any, list[str], str]], family: dict[str, str]) -> dict[str, Any]:
    """rows = (item id, pipeline primary slugs, silver slug or "none").

    strict: the silver entity is among the pipeline's primaries ("none" ↔ no primaries).
    family: same, but a product and its parent company count as a match (sonatype-firewall ↔
    sonatype), because the pipeline always adds the source's company as primary.
    """

    def top(slug: str) -> str:
        return family.get(slug, slug)

    strict = lenient = 0
    misses = []
    for key, primaries, silver in rows:
        if silver == "none":
            ok = not primaries
            ok_family = ok
        else:
            ok = silver in primaries
            ok_family = ok or top(silver) in {top(p) for p in primaries}
        strict += ok
        lenient += ok_family
        if not ok:
            misses.append(
                {"id": key, "silver": silver, "pipeline": primaries, "family_match": ok_family}
            )
    return {
        "n": len(rows),
        "strict": ratio(strict, len(rows)),
        "family": ratio(lenient, len(rows)),
        "misses": misses,
    }


# ---------- verifier: are partials about facts or about the JFrog-side analysis? ----------

_WORD = re.compile(r"[a-z0-9$][a-z0-9$.,%/-]*[a-z0-9%]|[a-z0-9]")
_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "by",
        "from",
        "that",
        "this",
        "is",
        "are",
        "was",
        "were",
        "be",
        "it",
        "its",
        "as",
        "at",
        "not",
        "no",
        "but",
        "which",
        "who",
        "what",
        "than",
        "then",
        "also",
        "into",
        "about",
        "over",
        "more",
        "most",
        "can",
        "may",
        "will",
        "would",
        "should",
        "could",
        "has",
        "have",
        "had",
        "their",
        "there",
        "they",
        "them",
        "these",
        "those",
        "such",
    ]
)
# Words that mark a statement as JFrog-side analysis rather than a fact from the source.
ANALYSIS_TERMS = (
    "jfrog",
    "artifactory",
    "xray",
    "curation",
    "battlecard",
    "competitive",
    "competitor",
    "position",
    "positioning",
    "differentiat",
    "opportunity",
    "threat",
    "gap",
    "customers",
)


def words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


@dataclass(frozen=True)
class ClaimSource:
    field: str  # "summary" / "why_jfrog_cares" / "quote" / "unclear"
    analysis: bool  # attributed to why_jfrog_cares and phrased as JFrog-side analysis


def attribute_claim(claim: str, summary: str, why: str, quote: str = "") -> ClaimSource:
    """Which checked text an unsupported claim came from: the field prefix if the verifier gave
    one (verify_signal.v2), else word overlap (heuristic).

    A claim counts as JFrog-side analysis if it overlaps most with why_jfrog_cares and mentions
    JFrog, its products or competitive-positioning terms that the summary does not use.
    """
    low_claim, low_summary = claim.lower(), summary.lower()
    jfrog_side = any(t in low_claim and t not in low_summary for t in ANALYSIS_TERMS)
    # verify_signal.v2 names the field itself: "summary: …" / "why_jfrog_cares: …".
    for field in ("summary", "why_jfrog_cares"):
        if low_claim.startswith(field + ":"):
            return ClaimSource(field, field == "why_jfrog_cares" and jfrog_side)
    cw = words(claim)
    if not cw:
        return ClaimSource("unclear", False)
    scores = {
        "summary": len(cw & words(summary)) / len(cw),
        "why_jfrog_cares": len(cw & words(why)) / len(cw),
        "quote": len(cw & words(quote)) / len(cw) if quote else 0.0,
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < 0.25:
        best = "unclear"
    if best != "why_jfrog_cares" and jfrog_side and scores["why_jfrog_cares"] >= 0.25:
        best = "why_jfrog_cares"  # a JFrog-side statement can only come from the analysis
    return ClaimSource(best, best == "why_jfrog_cares" and jfrog_side)


def verifier(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """items: {verdict, unsupported_claims, summary, why, quote, id, title}.

    analysis_only = partial verdicts whose every unsupported claim is JFrog-side analysis
    (which verify_signal.v1 says is not judged)."""
    verdicts = Counter(i["verdict"] for i in items)
    n = len(items)
    partials = [i for i in items if i["verdict"] == "partial"]
    listed, analysis_only, why_only = [], 0, 0
    for i in partials:
        sources = [
            attribute_claim(c, i["summary"], i["why"], i.get("quote", ""))
            for c in i["unsupported_claims"]
        ]
        only_analysis = bool(sources) and all(s.analysis for s in sources)
        only_why = bool(sources) and all(s.field == "why_jfrog_cares" for s in sources)
        analysis_only += only_analysis
        why_only += only_why
        listed.append(
            {
                "id": i["id"],
                "title": i["title"],
                "analysis_only": only_analysis,
                "claims": [
                    {"claim": c, "field": s.field, "analysis": s.analysis}
                    for c, s in zip(i["unsupported_claims"], sources, strict=True)
                ],
            }
        )
    return {
        "n": n,
        "verdicts": dict(verdicts),
        "partial_or_unsupported": ratio(verdicts["partial"] + verdicts["unsupported"], n),
        "partials": len(partials),
        "analysis_only_partials": analysis_only,
        "why_only_partials": why_only,
        "partial_details": listed,
    }


def agreement_matrix(a: Sequence[str], b: Sequence[str]) -> dict[str, Any]:
    pairs = Counter(f"{x} / {y}" for x, y in zip(a, b, strict=True))
    same = sum(x == y for x, y in zip(a, b, strict=True))
    return {"n": len(a), "agreement": ratio(same, len(a)), "pairs": dict(pairs.most_common())}


# ---------- dedup threshold sweep ----------


def thresholds() -> list[float]:
    return [round(0.60 + 0.05 * i, 2) for i in range(8)]  # 0.60 … 0.95


def dedup_sweep(
    pairs: Sequence[tuple[float, bool, str, int]], *, min_precision: float = 0.9
) -> dict[str, Any]:
    """pairs = (similarity, silver same_story, band, band_population).

    For each threshold t: predicted duplicate = similarity >= t. Raw counts are over the sample;
    precision/recall are weighted by band_population / sampled-in-band, i.e. estimates for all
    cross-source pairs in [0.60, 0.95) (pairs >= 0.95 are outside the sample).
    Recommended: the lowest threshold whose weighted precision >= min_precision (most recall at
    acceptable precision: a false merge hides a distinct story); if none, the best weighted F1.
    """
    sampled = Counter(b for _, _, b, _ in pairs)
    weight = {b: pop / sampled[b] for _, _, b, pop in pairs}
    rows = []
    for t in thresholds():
        tp = fp = fn = 0
        wtp = wfp = wfn = 0.0
        for sim, same, band, _ in pairs:
            w = weight[band]
            pred = sim >= t
            if pred and same:
                tp, wtp = tp + 1, wtp + w
            elif pred and not same:
                fp, wfp = fp + 1, wfp + w
            elif same:
                fn, wfn = fn + 1, wfn + w
        p, r = ratio(wtp, wtp + wfp), ratio(wtp, wtp + wfn)
        f1 = round(2 * p * r / (p + r), 3) if p and r else None
        rows.append(
            {"threshold": t, "tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}
        )
    ok = [r for r in rows if r["precision"] is not None and r["precision"] >= min_precision]
    if ok:
        best, rule = (
            min(ok, key=lambda r: r["threshold"]),
            f"lowest with precision ≥ {min_precision}",
        )
    else:
        scored = [r for r in rows if r["f1"] is not None]
        best = max(scored, key=lambda r: (r["f1"], r["threshold"])) if scored else None
        rule = "best F1 (no threshold reached the precision target)"
    positives = sum(1 for _, same, _, _ in pairs if same)
    return {
        "n": len(pairs),
        "positives": positives,
        "rows": rows,
        "recommended": best["threshold"] if best else None,
        "rule": rule,
    }
