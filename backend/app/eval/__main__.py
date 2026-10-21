"""Evaluation CLI (S8). Paid steps print their estimated calls and cost first.

python -m app.eval select [--force]        eval set → eval/silver_labels.csv (no LLM)
python -m app.eval embed [--estimate]      embed the full catalog (paid: embeddings)
python -m app.eval pairs [--force]         dedup pairs → eval/dedup_pairs.csv (no LLM)
python -m app.eval label [--estimate]      silver labels for items + pairs (paid: synthesis)
python -m app.eval run [--estimate]        eval pass + report (paid: bulk + verify, cached)
python -m app.eval compare A.json B.json   headline metrics of two runs side by side
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Signal


def _factory() -> sessionmaker:
    from app.db.session import get_engine

    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def cmd_select(args: argparse.Namespace) -> int:
    from app.eval import select as sel
    from app.eval.files import labels_path, read_labels, write_labels

    path = labels_path()
    if path.exists() and any(r.is_labeled() for r in read_labels()) and not args.force:
        print(f"{path} already has labels; use --force to reselect (labels are lost)")
        return 1
    with _factory()() as s:
        rows = sel.to_rows(sel.select_eval_set(sel.load_candidates(s)))
    write_labels(rows)
    print(f"{len(rows)} signals → {path}")
    for k, v in sel.summary(rows).items():
        print(f"  {k}: {v}")
    return 0


def cmd_embed(args: argparse.Namespace) -> int:
    from app.core.catalog import load_llm_routing
    from app.eval.common import count_tokens, eval_run, price
    from app.pipeline.context import RunContext
    from app.pipeline.embed import embed_signals, embedding_input
    from app.pipeline.scope import full_catalog

    factory = _factory()
    model = load_llm_routing().embedding.model
    with factory() as s:
        cfg = RunContext.create(None, session_factory=factory).config.embedding
        rows = s.execute(
            select(Signal.title, Signal.raw_text).where(
                full_catalog(),
                or_(Signal.embedding.is_(None), Signal.embedding_model != model),
            )
        ).all()
        total = s.scalar(select(func.count()).select_from(Signal))
    texts = [embedding_input(r.title, r.raw_text, cfg.max_chars) for r in rows]
    tokens = sum(count_tokens(model, [{"role": "user", "content": t}]) for t in texts)
    batches = -(-len(texts) // cfg.batch_size)
    print(
        f"embed: {len(texts)} of {total} signals without an embedding, {batches} requests, "
        f"{tokens:,} tokens, est. ${price(model, tokens, 0):.4f} ({model})"
    )
    if args.estimate or not texts:
        return 0
    with eval_run(factory, "eval_embed") as counts:
        ctx = RunContext.create(counts["run_id"], session_factory=factory)
        with factory.begin() as s:
            counts.update(embed_signals(s, ctx, where=full_catalog()))
    print(f"run {counts['run_id']}: {counts}")
    return 0


def cmd_pairs(args: argparse.Namespace) -> int:
    from app.core.catalog import load_llm_routing, load_pipeline
    from app.eval import pairs as pr
    from app.eval.files import pairs_path, read_pairs, write_pairs

    path = pairs_path()
    if path.exists() and any(p.is_labeled() for p in read_pairs()) and not args.force:
        print(f"{path} already has labels; use --force to resample (labels are lost)")
        return 1
    with _factory()() as s:
        pop = pr.population(
            s,
            window_days=load_pipeline().dedup.window_days,
            model=load_llm_routing().embedding.model,
        )
        rows = pr.to_rows(s, pr.sample(pop))
    write_pairs(rows)
    by_band: dict[str, int] = {}
    for p in pop:
        if (b := pr.band_of(p.similarity)) is not None:
            by_band[b] = by_band.get(b, 0) + 1
    print(
        f"{len(pop)} cross-source pairs in [0.60, 0.95) within the window; "
        f"{len(rows)} sampled → {path}"
    )
    for b in sorted(by_band):
        print(f"  {b}: {by_band[b]} pairs, {sum(r.band == b for r in rows)} sampled")
    return 0


def cmd_label(args: argparse.Namespace) -> int:
    from app.eval import silver
    from app.eval.files import pairs_path, read_labels, read_pairs

    factory = _factory()
    items = silver.estimate_items(factory, read_labels())
    pairs = read_pairs() if pairs_path().exists() else []
    pair_est = silver.estimate_pairs(factory, pairs)
    print(items.line("silver labels, items"))
    print(pair_est.line("silver labels, pairs"))
    if args.estimate:
        return 0
    print("items:", silver.label_items(factory))
    if pairs:
        print("pairs:", silver.label_pairs(factory))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from app.eval import report, runner

    factory = _factory()
    main_est, off_est = runner.estimate(factory)
    print(main_est.line("eval pass (triage + enrich + verify, upper bound)"))
    print(off_est.line(f"verify pass with thinking {runner.thinking_levels()[1]} (upper bound)"))
    if args.estimate:
        return 0
    results = runner.compare(factory, runner.run_pass(factory))
    path = report.write(results)
    paid = sum(c["paid_now_usd"] for calls in results["calls"].values() for c in calls)
    print(f"report → {path} (new spend ${paid:.4f})")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from app.eval.report import compare_runs

    a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.a, args.b))
    print(compare_runs(a, b))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.eval",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("select", "pairs"):
        sub.add_parser(name).add_argument("--force", action="store_true")
    for name in ("embed", "label", "run"):
        sub.add_parser(name).add_argument(
            "--estimate", action="store_true", help="print calls and cost, make no calls"
        )
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    handlers = {
        "select": cmd_select,
        "embed": cmd_embed,
        "pairs": cmd_pairs,
        "label": cmd_label,
        "run": cmd_run,
        "compare": cmd_compare,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
