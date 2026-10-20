"""Versioned prompt templates in app/prompts/<name>.v<N>.md.

File format:
    ---                      (optional YAML front matter)
    description: ...
    ---
    ## system                (optional)
    ...
    ## user                  (required)
    ... $placeholder ...

Placeholders use string.Template ($name / ${name}), so JSON braces need no escaping.
Write $$ for a literal "$".
Pass `prompt_version=prompt.id` (e.g. "enrich_signal.v1") to complete() so llm_calls records it.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_FILE = re.compile(r"^(?P<name>[a-z0-9_]+)\.v(?P<version>[1-9]\d*)\.md$")
_SECTION = re.compile(r"^##\s+(system|user)\s*$", re.IGNORECASE | re.MULTILINE)
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


class PromptError(ValueError):
    """Prompt file missing, malformed, or rendered with missing variables."""


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    user: str
    system: str | None = None
    description: str = ""

    @property
    def id(self) -> str:
        return f"{self.name}.v{self.version}"

    def messages(self, **variables: Any) -> list[dict[str, str]]:
        try:
            msgs = []
            if self.system:
                system = Template(self.system).substitute(variables)
                msgs.append({"role": "system", "content": system})
            msgs.append({"role": "user", "content": Template(self.user).substitute(variables)})
        except KeyError as e:
            raise PromptError(f"prompt {self.id}: missing variable {e.args[0]!r}") from None
        except ValueError as e:
            raise PromptError(f"prompt {self.id}: {e}") from None
        return msgs


def _versions(name: str, prompts_dir: Path) -> dict[int, Path]:
    found = {}
    for path in prompts_dir.glob(f"{name}.v*.md"):
        m = _FILE.match(path.name)
        if m and m["name"] == name:
            found[int(m["version"])] = path
    return found


def _parse(name: str, version: int, path: Path) -> Prompt:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, Any] = {}
    fm = _FRONT_MATTER.match(text)
    if fm:
        meta = yaml.safe_load(fm.group(1)) or {}
        text = text[fm.end() :]
    parts = _SECTION.split(text)
    if parts[0].strip():
        raise PromptError(f"{path.name}: text before the first '## system' / '## user' heading")
    sections: dict[str, str] = {}
    for heading, body in zip(parts[1::2], parts[2::2], strict=True):
        key = heading.lower()
        if key in sections:
            raise PromptError(f"{path.name}: duplicate '## {key}' section")
        sections[key] = body.strip()
    if not sections.get("user"):
        raise PromptError(f"{path.name}: missing '## user' section")
    return Prompt(
        name=name,
        version=version,
        user=sections["user"],
        system=sections.get("system") or None,
        description=str(meta.get("description", "")),
    )


@lru_cache
def _load(name: str, version: int | None, prompts_dir: Path) -> Prompt:
    versions = _versions(name, prompts_dir)
    if not versions:
        raise PromptError(f"no prompt files named {name}.v<N>.md in {prompts_dir}")
    chosen = max(versions) if version is None else version
    if chosen not in versions:
        raise PromptError(f"prompt {name}.v{chosen} not found (have {sorted(versions)})")
    return _parse(name, chosen, versions[chosen])


def load_prompt(name: str, version: int | None = None, prompts_dir: Path | None = None) -> Prompt:
    """Latest version of `name`, or the pinned `version`."""
    return _load(name, version, prompts_dir or PROMPTS_DIR)
