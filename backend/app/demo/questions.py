"""The curated Ask questions demo mode answers (config/demo.yaml)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings


class DemoFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ask_questions: list[str] = Field(default_factory=list)


@lru_cache
def _load(config_dir: Path) -> DemoFile:
    path = config_dir / "demo.yaml"
    if not path.exists():
        return DemoFile()
    return DemoFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def demo_questions() -> list[str]:
    return list(_load(get_settings().config_dir).ask_questions)


def _norm(text: str) -> str:
    text = text.replace("\u2019", "'")  # curly apostrophe
    return re.sub(r"[\s?.!]+$", "", " ".join(text.lower().split()))


def match_demo_question(question: str) -> str | None:
    """The curated question's exact text (the cache key uses it), or None."""
    want = _norm(question)
    return next((q for q in demo_questions() if _norm(q) == want), None)


DEMO_ASK_MESSAGE = (
    "Demo mode answers a few curated questions from the snapshot (no API keys). Try one of these, "
    "or run FrogRadar with your own keys (make live) to ask anything."
)
DEMO_MISSION_MESSAGE = (
    "Demo mode: new research missions need API keys (LLM, plus Tavily for web search). The "
    "finished missions are viewable. Run with your own keys (make live) to start one "
    "(about $0.03 each)."
)
DEMO_MISS_MESSAGE = (
    "Demo mode: this action needs a live model call, and the snapshot has no cached answer for it. "
    "Run with your own keys (make live) to use it."
)
