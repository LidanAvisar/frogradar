"""Rule-based entity linking: word boundaries, ambiguous aliases, product over company, primary."""

import pytest
from sqlalchemy import insert, select

from app.db.enums import EntityReviewStatus
from app.db.models import Signal, signal_entities
from app.pipeline.link import link_signals
from tests.pipeline.factories import ctx, entity, signal, source

pytestmark = pytest.mark.db

STRICT = {"enabled": True, "case_sensitive": True, "min_mentions": 3, "title_suffices": True}


def _links(s, sig: Signal) -> dict[str, bool]:
    """{slug: is_primary} for a signal."""
    from app.db.models import Entity

    rows = s.execute(
        select(Entity.slug, signal_entities.c.is_primary)
        .join(Entity, Entity.id == signal_entities.c.entity_id)
        .where(signal_entities.c.signal_id == sig.id)
    ).all()
    return dict(rows)


@pytest.fixture
def world(db_session):
    s = db_session
    github = entity(s, "github", "GitHub", rules={"GitHub": STRICT})
    entity(s, "github-actions", "GitHub Actions", parent=github)
    entity(
        s, "github-packages", "GitHub Packages", parent=github,
        aliases=["GHCR"], rules={"GHCR": {**STRICT, "min_mentions": 1}},
    )  # fmt: skip
    entity(s, "socket", "Socket", aliases=["socket.dev"], rules={"Socket": STRICT})
    entity(s, "snyk", "Snyk")
    entity(s, "microsoft", "Microsoft", rules={"Microsoft": {**STRICT, "enabled": False}})
    entity(s, "candidate-co", "Candidate Co", status=EntityReviewStatus.CANDIDATE)
    return s, source(s, "Press")


def test_word_boundaries(world):
    s, press = world
    sig = signal(s, press, "News", text="We use WebSocket and snyker tools. Snyk's report.")
    link_signals(s, ctx())
    # "WebSocket" is not Socket, "snyker" is not Snyk; "Snyk's" is.
    assert _links(s, sig) == {"snyk": False}


def test_ambiguous_alias_needs_case_and_mentions(world):
    s, press = world
    lower = signal(s, press, "Sockets", text="open a socket, a socket, a socket")
    two = signal(s, press, "Security", text="Socket found it. Socket said so.")
    three = signal(s, press, "Security", text="Socket found it. Socket said so. Socket again.")
    titled = signal(s, press, "Socket raises funding", text="The company said...")
    domain = signal(s, press, "Tools", text="see socket.dev for details")
    link_signals(s, ctx())
    assert _links(s, lower) == {}  # case-sensitive
    assert _links(s, two) == {}  # below min_mentions
    assert _links(s, three) == {"socket": False}
    assert _links(s, titled) == {"socket": True}  # a title match suffices
    assert _links(s, domain) == {"socket": False}  # unambiguous alias, default rule


def test_disabled_name_never_links(world):
    s, press = world
    sig = signal(s, press, "Microsoft ships a laptop", text="Microsoft Microsoft Microsoft")
    link_signals(s, ctx())
    assert _links(s, sig) == {}


def test_only_approved_entities(world):
    s, press = world
    sig = signal(s, press, "Candidate Co launches", text="Candidate Co")
    link_signals(s, ctx())
    assert _links(s, sig) == {}


def test_product_preferred_over_company(world):
    s, press = world
    # "GitHub" inside "GitHub Actions" counts for the product only.
    sig = signal(
        s, press, "GitHub Actions gets faster runners",
        text="GitHub Actions now... GitHub Actions also... GitHub Actions and GHCR.",
    )  # fmt: skip
    link_signals(s, ctx())
    assert _links(s, sig) == {"github-actions": True, "github-packages": False}
    meta = s.get(Signal, sig.id).meta["entity_links"]["links"]
    actions = next(link for link in meta if link["slug"] == "github-actions")
    assert actions["term"] == "GitHub Actions" and actions["where"] == "title+text"
    assert actions["mentions"] == 4


def test_company_in_title_with_product_in_text_keeps_both(world):
    s, press = world
    sig = signal(s, press, "GitHub outage", text="GitHub Actions jobs were delayed.")
    link_signals(s, ctx())
    assert _links(s, sig) == {"github": True, "github-actions": False}


def test_source_entity_is_primary(db_session):
    s = db_session
    sonatype = entity(s, "sonatype", "Sonatype")
    snyk = entity(s, "snyk", "Snyk")
    blog = source(s, "Sonatype Blog", entity=sonatype)
    sig = signal(s, blog, "Malware index", text="Compared with Snyk, Sonatype found...")
    # Collection already linked the source entity (S4); the linker keeps it primary.
    s.execute(
        insert(signal_entities).values(signal_id=sig.id, entity_id=sonatype.id, is_primary=True)
    )
    link_signals(s, ctx())
    assert _links(s, sig) == {"sonatype": True, "snyk": False}
    assert snyk.id  # mention, not primary: not the source entity and not in the title


def test_rerun_is_idempotent_and_relinks_on_change(world):
    s, press = world
    sig = signal(s, press, "Snyk news", text="Snyk")
    first = link_signals(s, ctx())
    again = link_signals(s, ctx())
    assert first["linked"] == 1 and again["linked"] == 0 and again["unchanged"] == 1
    assert _links(s, sig) == {"snyk": True}

    # The text changes (collector update): links are recomputed, stale ones removed.
    obj = s.get(Signal, sig.id)
    obj.title, obj.raw_text, obj.content_hash = "Socket news", "Socket", "f" * 64
    s.flush()
    counts = link_signals(s, ctx())
    assert counts["links_removed"] == 1
    assert _links(s, sig) == {"socket": True}


def test_links_from_other_stages_are_kept(world):
    s, press = world
    llm_found = entity(s, "chainguard", "Chainguard")
    sig = signal(s, press, "Snyk news", text="nothing else")
    s.execute(
        insert(signal_entities).values(signal_id=sig.id, entity_id=llm_found.id, is_primary=False)
    )
    link_signals(s, ctx())
    obj = s.get(Signal, sig.id)
    obj.content_hash = "e" * 64  # force a re-link
    s.flush()
    link_signals(s, ctx())
    assert _links(s, sig) == {"snyk": True, "chainguard": False}
