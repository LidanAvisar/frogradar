"""Startup check: the model keys FrogRadar needs are present (presence only, never values).

    python -m app.core.preflight     # exit 0, or exit 1 with one message naming the missing keys

Required = the provider keys of the four model tiers in config/llm_routing.yaml (after LLM_MODEL_*
overrides): with the default routing ANTHROPIC_API_KEY (bulk + synthesis) and OPENAI_API_KEY
(verify + embeddings). Optional keys (TAVILY_API_KEY, GITHUB_TOKEN, MCP_TOKEN) never stop the app;
a missing one turns only its feature off or degraded (app.core.status). `make up`, the backend
container and the scheduler run this first.
"""

from __future__ import annotations

import sys

from app.core.status import Status, get_status


def missing_message(st: Status) -> str | None:
    """One message naming each missing required key and the models that need it, or None."""
    missing = st.missing_required()
    if not missing:
        return None
    needs = []
    for name in missing:
        tiers = [m for m in st.models if not m.key_present and name in m.env_vars]
        what = ", ".join(f"{m.tier} {m.model}" for m in tiers)
        needs.append(f"{name} (for {what})")
    keys = " and ".join(needs)
    return (
        f"FrogRadar can't start: missing {keys}. Add it to .env in the repository root "
        "(copy .env.example to .env if you haven't), then run `make up` again."
    )


def main() -> int:
    msg = missing_message(get_status())
    if msg:
        print(msg, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
