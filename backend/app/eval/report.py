"""Render eval results to eval/results/<date>_<prompt versions>.md (+ .json), and compare runs."""

import json
from pathlib import Path
from typing import Any

from app.eval.files import results_dir

COMPARE_NOTE = """\
## How to compare two runs

Each run writes `eval/results/<date>_<prompt versions>.md` and a `.json` with the same keys, so a
prompt change shows up as a new file name. To compare two runs:

    make eval-compare A=eval/results/<old>.json B=eval/results/<new>.json

It prints the headline metrics side by side. Both runs must use the same silver labels
(`label_source` and `set` in the JSON) for the numbers to be comparable; `git diff` on the two
`.md` files shows the item-level changes (disagreement lists).
"""

HEADLINE = [
    ("Triage agreement", ("triage", "agreement")),
    ("Triage precision (vs silver)", ("triage", "precision")),
    ("Triage recall (vs silver)", ("triage", "recall")),
    ("Category agreement", ("category", "agreement")),
    ("Impact exact", ("impact", "exact")),
    ("Impact within ±1", ("impact", "within_1")),
    ("Impact mean signed error", ("impact", "mean_signed_error")),
    ("Primary entity (strict)", ("entity", "strict")),
    ("Primary entity (product ↔ company)", ("entity", "family")),
    ("Evidence quote verified", ("quotes", "verified")),
    ("Evidence quote repaired (D39)", ("quotes", "repaired")),
    ("Verifier partial or unsupported", ("verifier", "partial_or_unsupported")),
    ("Partials only from JFrog-side analysis", ("verifier", "analysis_only_partials")),
    ("Verifier thinking A/B agreement", ("thinking", "matrix", "agreement")),
    ("Dedup recommended threshold", ("dedup", "recommended")),
]


def get(d: Any, path: tuple[str, ...]) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def fmt(v: Any) -> str:
    """Ratios (0-1) as percentages; None as –; everything else as is."""
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.0%}"
    return str(v)


def num(v: Any) -> str:
    """Signed numbers (errors): +0.25, never a percentage."""
    return "–" if v is None else f"{v:+.2f}"


SIGNED = {("impact", "mean_signed_error")}
PLAIN = {("dedup", "recommended")}  # a similarity, not a ratio


def formatter(path: tuple[str, ...]):
    if path in SIGNED:
        return num
    if path in PLAIN:
        return lambda v: "–" if v is None else f"{v:.2f}"
    return fmt


def file_stem(results: dict[str, Any]) -> str:
    versions = "_".join(results["prompt_versions"].values())
    return f"{results['date']}_{versions}"


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return out


def _cost_rows(calls: list[dict[str, Any]]) -> list[list[Any]]:
    return [
        [
            c["model"],
            c["operation"],
            c["calls"],
            c["cache_hits"],
            c["failed"],
            f"{c['tokens_in']:,}",
            f"{c['tokens_out']:,}",
            f"{c['tokens_reasoning']:,}",
            f"${c['work_usd']:.4f}",
            f"${c['paid_now_usd']:.4f}",
            c["median_latency_ms"] or "–",
        ]
        for c in calls
    ]


COST_HEADER = [
    "model",
    "operation",
    "calls",
    "cache hits",
    "failed",
    "tokens in",
    "tokens out",
    "reasoning",
    "cost of the work",
    "paid in this run",
    "median latency ms",
]


def _mode_totals(calls: list[dict[str, Any]]) -> dict[str, Any]:
    v = [c for c in calls if c["operation"] == "verify_signal"]
    lat = [c["median_latency_ms"] for c in v if c["median_latency_ms"]]
    return {
        "calls": sum(c["calls"] for c in v),
        "tokens_out": sum(c["tokens_out"] for c in v),
        "tokens_reasoning": sum(c["tokens_reasoning"] for c in v),
        "usd": sum(c["work_usd"] for c in v),
        "latency": lat[0] if lat else None,
    }


def render(r: dict[str, Any]) -> str:
    s = r["set"]
    md = [
        f"# FrogRadar eval – {r['date']}",
        "",
        "**These are agreement scores with a stronger model (silver labels), not human-verified "
        "accuracy.** Reference labels: " + ", ".join(f"`{x}`" for x in r["label_source"]) + ".",
        "",
        "Prompt versions: "
        + ", ".join(f"`{v}`" for v in r["prompt_versions"].values())
        + f". Eval runs: pipeline #{r['run_ids']['pipeline']}, "
        f"verify with thinking {r['thinking']['alternative']} #{r['run_ids']['verify_alt']}.",
        "",
        f"Set: {s['items']} signals ({s['in_scope']} in the demo scope, "
        f"{s['items'] - s['in_scope']}"
        f" from inactive sources; {s['silver_relevant']} relevant per silver label) and "
        f"{s['pairs']} dedup pairs ({s['same_story_pairs']} same story).",
        "",
        "## Summary",
        "",
        *_table(
            ["metric", "value"], [[name, formatter(path)(get(r, path))] for name, path in HEADLINE]
        ),
        "",
    ]
    if r["failures"]:
        md += [
            "Items that failed in the pass: "
            + ", ".join(f"#{f['id']} ({f['error']})" for f in r["failures"]),
            "",
        ]

    t = r["triage"]
    md += [
        "## Triage",
        "",
        f"{t['n']} items: {t['tp']} both relevant, {t['tn']} both irrelevant, {t['fp']} relevant "
        f"only for the pipeline (its recall bias, D34), {t['fn']} relevant only for the silver "
        f"label (possible misses). Precision {fmt(t['precision'])}, recall {fmt(t['recall'])}.",
        "",
        "Every disagreement:",
        "",
        *_table(
            ["#", "title", "source", "pipeline", "silver", "silver reason", "pipeline reason"],
            [
                [
                    d["id"],
                    d["title"][:70],
                    d["source"],
                    d["pipeline"],
                    d["silver"],
                    d["silver_reason"],
                    d["pipeline_reason"],
                ]
                for d in t["disagreements"]
            ],
        ),
        "",
    ]

    c = r["category"]
    md += [
        "## Category",
        "",
        f"{c['n']} items relevant for both and enriched; agreement {fmt(c['agreement'])}.",
        "",
    ]
    if c["mismatches"]:
        md += [
            "Mismatches (silver → pipeline): "
            + "; ".join(f"{k} ×{v}" for k, v in c["confusion"].items()),
            "",
        ]
        md += _table(
            ["#", "title", "silver", "pipeline"],
            [[m["id"], m["title"][:70], m["silver"], m["pipeline"]] for m in c["mismatches"]],
        ) + [""]

    i = r["impact"]
    md += [
        "## Impact",
        "",
        f"Exact {fmt(i['exact'])}, within ±1 {fmt(i['within_1'])}, mean signed error "
        f"{num(i['mean_signed_error'])} (pipeline − silver; > 0 = the pipeline rates higher), "
        f"mean absolute error {num(i['mean_abs_error']).lstrip('+')}. Error distribution: "
        + ", ".join(f"{int(k):+d}: {v}" for k, v in i["errors"].items())
        + ".",
        "",
    ]

    e = r["entity"]
    md += [
        "## Primary entity",
        "",
        f"Strict {fmt(e['strict'])} (the silver entity is among the pipeline's primaries), "
        f"{fmt(e['family'])} counting a product and its parent company as a match (the pipeline "
        "always adds the source's own company as primary).",
        "",
    ]
    if e["misses"]:
        md += _table(
            ["#", "title", "silver", "pipeline primaries", "product ↔ company"],
            [
                [
                    m["id"],
                    m["title"][:70],
                    m["silver"],
                    ", ".join(m["pipeline"]) or "none",
                    "yes" if m["family_match"] else "no",
                ]
                for m in e["misses"]
            ],
        ) + [""]

    q = r["quotes"]
    md += [
        "## Evidence quotes",
        "",
        f"{q['n']} enriched items: verbatim in the source {fmt(q['verified'])}, repaired in code "
        f"{fmt(q['repaired'])}, needed an LLM retry {fmt(q['retried'])}."
        + (
            f" Unverified: {', '.join(f'#{x}' for x in q['unverified_ids'])}."
            if q["unverified_ids"]
            else ""
        ),
        "",
    ]

    v = r["verifier"]
    md += [
        f"## Verifier (thinking {r['thinking']['configured']})",
        "",
        "Verdicts: "
        + ", ".join(f"{k} {n}" for k, n in sorted(v["verdicts"].items()))
        + f". Partial or unsupported: {fmt(v['partial_or_unsupported'])}.",
        "",
        f"Partials whose every unsupported claim is JFrog-side analysis (which the verify prompt "
        f"says is not judged): **{v['analysis_only_partials']} of {v['partials']}** "
        f"({v['why_only_partials']} have all claims in why_jfrog_cares). Heuristic: word overlap "
        "with summary vs why_jfrog_cares, plus JFrog/positioning terms. Every partial:",
        "",
    ]
    rows = []
    for p in v["partial_details"]:
        for cl in p["claims"] or [{"claim": "(no claims listed)", "field": "", "analysis": False}]:
            rows.append(
                [
                    p["id"],
                    p["title"][:50],
                    cl["claim"],
                    cl["field"],
                    "yes" if cl["analysis"] else "no",
                ]
            )
    md += _table(["#", "title", "unsupported claim", "from", "analysis"], rows) + [""]

    th = r["thinking"]
    low_t = _mode_totals(r["calls"]["pipeline"])
    off_t = _mode_totals(r["calls"]["verify_alt"])
    md += [
        f"## Verifier thinking: {th['configured']} (configured) vs {th['alternative']}",
        "",
        f"Same {th['matrix']['n']} items, same inputs. Verdict agreement "
        f"{fmt(th['matrix']['agreement'])} ("
        + ", ".join(f"{k}: {n}" for k, n in th["matrix"]["pairs"].items())
        + f", {th['configured']} / {th['alternative']}).",
        "",
        *_table(
            [
                "mode",
                "verify calls",
                "output tokens",
                "of which reasoning",
                "cost",
                "median latency ms",
                "partial/unsupported",
                "analysis-only partials",
            ],
            [
                [
                    th["configured"],
                    low_t["calls"],
                    f"{low_t['tokens_out']:,}",
                    f"{low_t['tokens_reasoning']:,}",
                    f"${low_t['usd']:.4f}",
                    low_t["latency"] or "–",
                    fmt(th["configured_verifier"]["partial_or_unsupported"]),
                    th["configured_verifier"]["analysis_only_partials"],
                ],
                [
                    th["alternative"],
                    off_t["calls"],
                    f"{off_t['tokens_out']:,}",
                    f"{off_t['tokens_reasoning']:,}",
                    f"${off_t['usd']:.4f}",
                    off_t["latency"] or "–",
                    fmt(th["alternative_verifier"]["partial_or_unsupported"]),
                    th["alternative_verifier"]["analysis_only_partials"],
                ],
            ],
        ),
        "",
    ]
    if th["disagreements"]:
        md += [
            "Disagreements: "
            + "; ".join(
                f"#{d['id']} {d['title'][:50]} "
                f"({th['configured']} {d['configured']}, {th['alternative']} {d['alternative']})"
                for d in th["disagreements"]
            ),
            "",
        ]
    if th["alternative_failures"]:
        md += [
            f"Thinking-{th['alternative']} failures: "
            + ", ".join(f"#{x}" for x in th["alternative_failures"]),
            "",
        ]

    d = r["dedup"]
    if d:
        md += [
            "## Dedup threshold (cross-source)",
            "",
            f"{d['n']} pairs sampled across similarity bands ({d['positives']} same story). "
            "Precision/recall are weighted by band population (estimates for all cross-source "
            "pairs "
            "in [0.60, 0.95) within the dedup window); raw counts are over the sample. Current "
            f"threshold {d['current']}; recommended **{d['recommended']}** ({d['rule']}).",
            "",
            *_table(
                ["band", "pairs in population", "sampled", "same story"],
                [[b["band"], b["population"], b["sampled"], b["same_story"]] for b in d["bands"]],
            ),
            "",
            *_table(
                ["threshold", "TP", "FP", "FN", "precision", "recall", "F1"],
                [
                    [
                        f"{x['threshold']:.2f}",
                        x["tp"],
                        x["fp"],
                        x["fn"],
                        fmt(x["precision"]),
                        fmt(x["recall"]),
                        fmt(x["f1"]),
                    ]
                    for x in d["rows"]
                ],
            ),
            "",
        ]

    md += [
        "## Calls and cost per model",
        "",
        '"Cost of the work" = what the calls cost when first made (cache hits are free now but '
        'show the original cost); "paid in this run" = new spend.',
        "",
    ]
    for name, calls in r["calls"].items():
        md += [
            f"Run {name} (#{r['run_ids'][name]}):",
            "",
            *_table(COST_HEADER, _cost_rows(calls)),
            "",
        ]
    md += [COMPARE_NOTE]
    return "\n".join(md)


def write(results: dict[str, Any]) -> Path:
    out = results_dir()
    out.mkdir(parents=True, exist_ok=True)
    stem = file_stem(results)
    (out / f"{stem}.json").write_text(
        json.dumps(results, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    path = out / f"{stem}.md"
    path.write_text(render(results) + "\n", encoding="utf-8")
    return path


def compare_runs(a: dict[str, Any], b: dict[str, Any]) -> str:
    lines = [
        f"{'metric':42} {'A':>10} {'B':>10}",
        f"{'(prompts)':42} {file_stem(a)[:10]:>10} {file_stem(b)[:10]:>10}",
    ]
    for name, path in HEADLINE:
        f = formatter(path)
        lines.append(f"{name:42} {f(get(a, path)):>10} {f(get(b, path)):>10}")
    if a.get("label_source") != b.get("label_source") or a.get("set") != b.get("set"):
        lines.append("WARNING: different label sets – numbers are not directly comparable")
    va, vb = a["prompt_versions"], b["prompt_versions"]
    changed = [f"{k}: {va.get(k)} → {vb.get(k)}" for k in vb if va.get(k) != vb.get(k)]
    lines.append("prompt changes: " + (", ".join(changed) if changed else "none"))
    return "\n".join(lines)
