"""Ask retrieval eval (S13): eval/ask_questions.yaml → recall@k, cited-expected, not-enough-evidence
correctness and cost per question → eval/results/ask_<timestamp>.{md,json}.

--retrieval-only runs filters + retrieval (question embeddings only, ~$0.00001 each).
--cap stops before a question when this run's spend plus a margin would cross the cap.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import sessionmaker

from app.core import clock as app_clock
from app.core.catalog import load_pipeline
from app.db.enums import AskSource
from app.eval.files import eval_dir, results_dir
from app.rag.filters import infer_filters
from app.rag.retrieve import Retrieval, retrieve
from app.rag.service import LLMFns, ask

MARGIN_USD = 0.05  # worst case of one more question


@dataclass
class Question:
    id: str
    question: str
    expected: list[str] = field(default_factory=list)
    not_enough_evidence: bool = False
    doc_type: str | None = None
    match: str = "all"  # "any": one expected id retrieved = full recall ("A, B or others")


@dataclass
class Row:
    id: str
    question: str
    expected: list[str]
    retrieved: list[str]
    hits: list[str]
    recall: float | None
    filters: dict[str, Any]
    expect_not_enough: bool
    not_enough: bool | None = None
    cited: list[str] = field(default_factory=list)
    cites_expected: bool | None = None
    ok: bool | None = None
    points: int | None = None
    cost_usd: float = 0.0
    cached: bool | None = None
    latency_s: float = 0.0
    turn_id: int | None = None
    answer: dict[str, Any] | None = None


def load_questions(path: Path | None = None) -> list[Question]:
    data = yaml.safe_load((path or eval_dir() / "ask_questions.yaml").read_text())
    return [Question(**q) for q in data["questions"]]


def matches(expected: str, doc_id: str) -> bool:
    return doc_id.startswith(expected[:-1]) if expected.endswith("*") else doc_id == expected


def score(q: Question, retrieved: list[str]) -> tuple[list[str], float | None]:
    hits = [e for e in q.expected if any(matches(e, d) for d in retrieved)]
    if not q.expected:
        return hits, None
    if q.match == "any":
        return hits, 1.0 if hits else 0.0
    return hits, len(hits) / len(q.expected)


def run(
    factory: sessionmaker,
    llm: LLMFns,
    *,
    retrieval_only: bool,
    cap: float,
    only: set[str] | None = None,
) -> list[Row]:
    cfg = load_pipeline().ask
    rows: list[Row] = []
    spent = 0.0
    for q in load_questions():
        if only and q.id not in only:
            continue
        if not retrieval_only and spent + MARGIN_USD > cap:
            print(f"stop before {q.id}: spent ${spent:.4f}, cap ${cap:.2f}")
            break
        now = app_clock.now()
        started = time.monotonic()
        captured: list[Retrieval] = []
        with factory() as s:
            if retrieval_only:
                f = infer_filters(s, q.question, now)
                found = retrieve(s, q.question, f, embed=llm.embed, cfg=cfg, now=now)
                captured.append(found)
                out = None
                filters = f.as_dict()
            else:
                out = ask(s, q.question, conversation_id=None, llm=llm, now=now, cfg=cfg,
                          on_retrieval=captured.append, source=AskSource.EVAL)  # fmt: skip
                filters = out.filters.model_dump()
        retrieved = [h.doc.id for h in captured[0].hits] if captured else []
        hits, recall = score(q, retrieved)
        row = Row(
            id=q.id, question=q.question, expected=q.expected, retrieved=retrieved, hits=hits,
            recall=recall, filters=filters, expect_not_enough=q.not_enough_evidence,
            latency_s=round(time.monotonic() - started, 2),
        )  # fmt: skip
        if out is not None:
            row.not_enough = out.not_enough_evidence
            row.cited = [s.id for s in out.sources]
            row.cites_expected = (
                any(matches(e, c) for e in q.expected for c in row.cited) if q.expected else None
            )
            row.ok = (
                out.not_enough_evidence
                if q.not_enough_evidence
                else (not out.not_enough_evidence and bool(row.cites_expected))
            )
            row.points = len(out.points)
            row.cost_usd = out.cost_usd or 0.0
            row.cached = out.cached
            row.turn_id = out.turn_id
            row.answer = out.model_dump(mode="json")
            spent += row.cost_usd
        rows.append(row)
        print(_line(row))
    return rows


def _line(r: Row) -> str:
    rec = "n/a" if r.recall is None else f"{r.recall:.2f}"
    tail = ""
    if r.not_enough is not None:
        tail = (
            f" | NEE {r.not_enough} | cites expected {r.cites_expected} | "
            f"{'OK' if r.ok else 'FAIL'} | ${r.cost_usd:.4f}"
        )
    return f"{r.id} recall@k {rec} ({len(r.hits)}/{len(r.expected)}){tail}"


def summary(rows: list[Row]) -> dict[str, Any]:
    answerable = [r for r in rows if r.recall is not None]
    answered = [r for r in rows if r.ok is not None]
    return {
        "questions": len(rows),
        "mean_recall_at_k": round(sum(r.recall for r in answerable) / len(answerable), 3)
        if answerable
        else None,
        "full_recall": sum(r.recall == 1 for r in answerable),
        "answers_ok": sum(bool(r.ok) for r in answered) if answered else None,
        "answered": len(answered),
        "cost_usd": round(sum(r.cost_usd for r in rows), 4),
    }


def write_report(rows: list[Row], k: int, retrieval_only: bool) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = results_dir() / f"ask_{stamp}{'_retrieval' if retrieval_only else ''}"
    s = summary(rows)
    base.with_suffix(".json").write_text(
        json.dumps({"summary": s, "k": k, "rows": [asdict(r) for r in rows]}, indent=2, default=str)
    )
    lines = [
        f"# Ask eval {stamp}{' (retrieval only)' if retrieval_only else ''}",
        "",
        f"k = {k}; mean recall@k {s['mean_recall_at_k']}; full recall {s['full_recall']}; "
        f"answers ok {s['answers_ok']} of {s['answered']}; cost ${s['cost_usd']:.4f}",
        "",
        "| id | question | filters | recall@k | answer | cites expected | ok | cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        f = ", ".join(v for v in (r.filters.get("company_name") or r.filters.get("company"),
                                  r.filters.get("window")) if v) or "-"  # fmt: skip
        rec = "n/a" if r.recall is None else f"{r.recall:.2f} ({len(r.hits)}/{len(r.expected)})"
        ans = "-" if r.not_enough is None else ("not enough" if r.not_enough else f"{r.points} pts")
        lines.append(
            f"| {r.id} | {r.question} | {f} | {rec} | {ans} | "
            f"{'-' if r.cites_expected is None else r.cites_expected} | "
            f"{'-' if r.ok is None else ('yes' if r.ok else 'no')} | ${r.cost_usd:.4f} |"
        )
    base.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return base
