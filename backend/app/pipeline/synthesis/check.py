"""Second-model check of synthesized claims (verify task: a different model family).

One call per output (the brief, or one battlecard): every claim that passed the citation check is
judged against the summaries / takeaways / analysis of the signals IT cites. supported → kept;
partial, unsupported (or no verdict) → dropped, with the verifier's note kept in the report.
"""

from collections.abc import Sequence

from app.core.llm import load_prompt
from app.pipeline.context import CompleteFn
from app.pipeline.prompting import delimit
from app.pipeline.synthesis.citations import Checked, Claim
from app.pipeline.synthesis.inputs import BLOCK_MAX_CHARS, SignalFacts
from app.pipeline.synthesis.schemas import check_schema

PROMPT = "verify_synthesis"


def verify_claims(
    claims: Sequence[Claim],
    facts_by_id: dict[int, SignalFacts],
    complete: CompleteFn,
    *,
    kind: str,
    subject: str,
) -> tuple[Checked, str | None]:
    """(kept/dropped, prompt version). No claims → no call."""
    out = Checked()
    if not claims:
        return out, None
    prompt = load_prompt(PROMPT)
    cited = sorted({i for c in claims for i in c.signal_ids})
    signals = "\n\n".join(facts_by_id[i].render() for i in cited)
    claims_text = "\n".join(
        f"{c.key}: {c.text} [cites {', '.join(f'#{i}' for i in c.signal_ids)}]" for c in claims
    )
    tag, body = delimit(f"{signals}\n\nCLAIMS:\n{claims_text}", BLOCK_MAX_CHARS)
    messages = prompt.messages(tag=tag, text=body, kind=kind, subject=subject)
    schema = check_schema([c.key for c in claims])
    result = complete("verify", PROMPT, messages, schema, prompt_version=prompt.id)
    verdicts = {v.key: v for v in result.items}  # type: ignore[attr-defined]
    for c in claims:
        v = verdicts.get(c.key)
        if v is None:
            out.drop(c, "not_verified")
        elif v.verdict == "supported":
            out.kept.append(c)
        else:
            out.drop(c, f"verifier_{v.verdict}", verifier_note=v.note)
    return out, prompt.id
