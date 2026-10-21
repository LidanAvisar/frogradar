"""Committed eval files (repo-root eval/): ids, titles, URLs, sources, dates and labels only.

- eval/silver_labels.csv   the eval set (40 signals) with silver labels from the synthesis model.
- eval/dedup_pairs.csv     cross-source signal pairs with a silver same_story label.
- eval/results/            one report per eval run (<date>_<prompt versions>.md + .json).

No article text or excerpts are written here (README "raw_text"): the labeler and the pipeline
read raw_text from the DB at run time. Writes are atomic (temp file + rename).
"""

import csv
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

NONE_ENTITY = "none"  # primary_entity label: the item is about no tracked entity


def eval_dir() -> Path:
    """Repo-root eval/ (EVAL_DIR overrides it, e.g. in tests)."""
    if env := os.environ.get("EVAL_DIR"):
        return Path(env)
    return Path(__file__).resolve().parents[3] / "eval"


def labels_path() -> Path:
    return eval_dir() / "silver_labels.csv"


def pairs_path() -> Path:
    return eval_dir() / "dedup_pairs.csv"


def results_dir() -> Path:
    return eval_dir() / "results"


@dataclass
class LabelRow:
    """One eval signal. Labels are empty until `make eval-label` fills them."""

    id: int
    title: str
    source: str
    date: str
    url: str
    in_scope: str  # y / n (demo scope at selection time)
    stratum: str  # why it was picked (selection only; never shown to the labeler)
    relevant: str = ""  # y / n
    category: str = ""
    impact: str = ""  # 1-5
    primary_entity: str = ""  # slug or "none"
    label_source: str = ""  # e.g. "model:anthropic/claude-sonnet-5-5 silver_label.v1"
    label_reason: str = ""  # the labeler's one-sentence reason (model-written, no source text)

    def is_labeled(self) -> bool:
        return self.relevant in ("y", "n")


@dataclass
class PairRow:
    """Two signals from different sources, published within the dedup window."""

    a_id: int
    b_id: int
    a_title: str
    b_title: str
    a_source: str
    b_source: str
    a_date: str
    b_date: str
    similarity: float
    band: str  # e.g. "0.80-0.85"
    band_population: int  # all cross-source pairs in this band (sampling weight)
    same_story: str = ""  # y / n
    label_source: str = ""
    label_reason: str = ""

    def is_labeled(self) -> bool:
        return self.same_story in ("y", "n")


def _columns(cls: type) -> list[str]:
    return [f.name for f in fields(cls)]


def _read(path: Path, cls: type) -> list[Any]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != _columns(cls):
            raise ValueError(f"{path.name}: expected {_columns(cls)}, got {reader.fieldnames}")
        types = {f.name: f.type for f in fields(cls)}
        rows = []
        for raw in reader:
            values: dict[str, Any] = {}
            for k, v in raw.items():
                v = v or ""
                t = types[k]
                values[k] = (
                    int(v) if t in (int, "int") else float(v) if t in (float, "float") else v
                )
            rows.append(cls(**values))
        return rows


def _write(path: Path, cls: type, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_columns(cls), lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)
    tmp.replace(path)


def read_labels(path: Path | None = None) -> list[LabelRow]:
    return _read(path or labels_path(), LabelRow)


def write_labels(rows: list[LabelRow], path: Path | None = None) -> None:
    _write(path or labels_path(), LabelRow, rows)


def read_pairs(path: Path | None = None) -> list[PairRow]:
    return _read(path or pairs_path(), PairRow)


def write_pairs(rows: list[PairRow], path: Path | None = None) -> None:
    _write(path or pairs_path(), PairRow, rows)
