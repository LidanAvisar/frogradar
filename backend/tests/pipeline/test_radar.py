"""Competitor Radar (S11): name keys, resolution against known entities, merging, emergence score,
profile citation / verifier checks, the radar step end to end, discovery signals in enrich and
scope."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.catalog import RadarMergeSettings, RadarScoreSettings, RadarSettings
from app.db.enums import (
    FetchMethod,
    ProfileStatus,
    RadarKind,
    RadarStatus,
    Relationship,
    SourceKind,
)
from app.db.models import Entity, RadarCandidate, RepoStarSnapshot, Signal, Source
from app.pipeline.enrich import enrich_signals, untracked
from app.pipeline.radar.extract import Observation, observations
from app.pipeline.radar.normalize import name_key, site_host
from app.pipeline.radar.profile import KEYS
from app.pipeline.radar.resolve import Known, Resolver, ratio
from app.pipeline.radar.score import Mention, ScoreInput, compute
from app.pipeline.radar.stars import Velocity
from app.pipeline.radar.step import radar_step
from app.pipeline.scope import visible
from tests.pipeline.factories import (
    T0,
    FakeEmbed,
    FakeLLM,
    claim_keys_in,
    ctx,
    ensure_capability,
    entity,
    rotated,
    signal,
    source,
)

pytestmark = pytest.mark.db

NOW = T0 + timedelta(days=10)
CFG = RadarSettings(ignore=["npm", "OpenAI"])


# ---------- name keys ----------


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Endor Labs", "endor"),
        ("EndorLabs", "endor"),
        ("socket.dev", "socket"),
        ("Socket Security", "socket"),
        ("github.com/RegShield/regshield", "regshield"),
        ("RegShield/regshield", "regshield"),
        ("GitLab", "gitlab"),  # "lab" is not a corporate suffix
        ("Trivio", "trivio"),  # "io" only as a domain (".io")
        ("Kubescape.io", "kubescape"),
        ("Zinc", "zinc"),  # a suffix is removed only if 3+ characters remain
        ("ComposioHQ", "composio"),
    ],
)
def test_name_key(name, key):
    assert name_key(name) == key


def test_shared_hosts_are_not_identity():
    assert site_host("https://github.com/a/b") is None
    assert site_host("https://medium.com/@x/post") is None
    assert site_host("https://www.RegShield.io/docs") == "regshield.io"


# ---------- resolution ----------


def _obs(
    name,
    sid,
    *,
    kind=RadarKind.PRODUCT,
    days=0,
    source_id=None,
    homepage=None,
    repo=None,
    context=None,
    segments=("supply_chain_security",),
    origin="news_new_entity",
    points=None,
):
    return Observation(
        name=name,
        kind=kind,
        signal_id=sid,
        source_id=source_id,
        at=T0 + timedelta(days=days),
        origin=origin,
        homepage=homepage,
        repo_url=repo,
        context=context,
        segments=segments,
        hn_points=points,
    )


@pytest.fixture
def known_world(db_session):
    s = db_session
    socket = entity(s, "socket", "Socket", aliases=["Socket Security", "socket.dev"])
    s.execute(update(Entity).where(Entity.id == socket.id).values(website="https://socket.dev"))
    gh = Source(
        name="Chainguard GitHub",
        url="https://github.com/chainguard-dev",
        kind=SourceKind.GITHUB,
        fetch_method=FetchMethod.GITHUB,
        trust_score=0.85,
        options={"repos": ["apko", "wolfi-dev/os"]},
    )
    s.add(gh)
    s.flush()
    return s


@pytest.mark.parametrize(
    ("obs", "reason"),
    [
        (_obs("Socket Security Inc", 1), "tracked_name"),
        (_obs("SocketDev", 1, homepage="https://www.socket.dev/blog"), "tracked_website"),
        (_obs("melange", 1, repo="https://github.com/chainguard-dev/melange"), "tracked_org"),
        (_obs("wolfi os", 1, repo="https://github.com/wolfi-dev/os"), "tracked_org"),
        (_obs("OpenAI", 1), "ignored"),
        (_obs("X", 1), "too_short"),
        (_obs("Regshield", 1), None),
    ],
)
def test_known_entities_are_skipped(known_world, obs, reason):
    assert Known.load(known_world, CFG).skip_reason(obs) == reason


def test_exact_repo_and_host_matches_attach_mentions(known_world):
    s = known_world
    src_a, src_b = source(s, "Press A"), source(s, "Press B")
    sigs = [signal(s, src_a, f"s{i}").id for i in range(4)]
    r = Resolver(s, CFG, embed=None, now=NOW)
    a = r.add(_obs("RegShield", sigs[0], source_id=src_a.id, context="Registry firewall."))
    assert r.add(_obs("Regshield Inc.", sigs[1], source_id=src_b.id, days=3)) is a  # exact key
    b = r.add(_obs("rs-proxy", sigs[2], repo="https://github.com/regshield/proxy"))
    assert (
        r.add(_obs("RegShield Proxy", sigs[3], repo="https://github.com/RegShield/proxy/tree/main"))
        is b
    )
    assert (
        r.counts["created"] == 2
        and r.counts["matched_exact"] == 1
        and r.counts["matched_repo"] == 1
    )
    assert a.mention_signal_ids == sorted(sigs[:2]) and a.source_ids == sorted([src_a.id, src_b.id])
    assert a.last_seen == T0 + timedelta(days=3)
    assert a.meta["mention_origins"] == {
        str(sigs[0]): "news_new_entity",
        str(sigs[1]): "news_new_entity",
    }
    assert b.merges[0]["rule"] == "repo" and b.meta["other_names"] == ["RegShield Proxy"]
    # Re-adding the same observation is a no-op (idempotent per signal).
    r.add(_obs("RegShield", sigs[0], source_id=src_a.id))
    assert a.mention_signal_ids == sorted(sigs[:2])


def _fuzzy_resolver(s, sim: float, name_ratio: float = 0.92) -> Resolver:
    embed = FakeEmbed(
        {
            "Kubeguard (product)": rotated(0),
            "Kubeguardx (product)": rotated(
                0 if sim >= 1 else __import__("math").degrees(__import__("math").acos(sim))
            ),
            "Chainwatch (product)": rotated(0, axis=2, toward=3),
            "Chainwatchio (product)": rotated(0, axis=2, toward=3),
        }
    )
    cfg = RadarSettings(merge=RadarMergeSettings(name_ratio=name_ratio, embedding_cosine=0.90))
    return Resolver(s, cfg, embed=lambda texts: embed(texts).vectors, now=NOW)


def test_fuzzy_merge_needs_both_thresholds(known_world):
    s = known_world
    ids = [signal(s, None, f"f{i}").id for i in range(6)]
    assert ratio("kubeguard", "kubeguardx") >= 0.92
    r = _fuzzy_resolver(s, sim=0.95)
    a = r.add(_obs("Kubeguard", ids[0]))
    assert r.add(_obs("Kubeguardx", ids[1])) is a and r.counts["matched_fuzzy"] == 1

    s.query(RadarCandidate).delete()
    r = _fuzzy_resolver(s, sim=0.85)  # names close, meaning not: no merge
    assert r.add(_obs("Kubeguard", ids[2])) is not r.add(_obs("Kubeguardx", ids[3]))

    # Identical embeddings but the names are only 0.91 alike: no merge either.
    assert 0.90 < ratio("chainwatch", "chainwatchio") < 0.92
    s.query(RadarCandidate).delete()
    r = _fuzzy_resolver(s, sim=1.0)
    assert r.add(_obs("Chainwatch", ids[4])) is not r.add(_obs("Chainwatchio", ids[5]))


def test_merge_pass_merges_newer_into_older_and_logs_both(known_world):
    s = known_world
    ids = [signal(s, None, f"m{i}").id for i in range(3)]
    r = Resolver(s, CFG, embed=None, now=NOW)
    old = r.add(_obs("Pkgguard", ids[0], days=0))
    new = r.add(_obs("PG Firewall", ids[1], days=2, homepage="https://pkgguard.dev/firewall"))
    dismissed = r.add(_obs("Other Thing", ids[2], days=3))
    # Homepages learned later (e.g. from a profile or another mention):
    old.homepage = "https://www.pkgguard.dev"
    dismissed.homepage = "https://pkgguard.dev/x"
    dismissed.status = RadarStatus.DISMISSED
    assert r.merge_pass() == 1
    assert new.merged_into_id == old.id
    assert old.mention_signal_ids == sorted(ids[:2])
    assert old.merges[-1]["from_id"] == new.id and old.merges[-1]["rule"] == "host"
    assert new.merges[-1]["into_id"] == old.id
    assert dismissed.merged_into_id is None  # analyst decisions are never merged away
    assert old.profile_signal_hash is None


def test_dismissed_candidate_collects_mentions_but_stays_dismissed(known_world):
    s = known_world
    ids = [signal(s, None, f"d{i}").id for i in range(2)]
    r = Resolver(s, CFG, embed=None, now=NOW)
    c = r.add(_obs("Regshield", ids[0]))
    c.status = RadarStatus.DISMISSED
    s.flush()
    r2 = Resolver(s, CFG, embed=None, now=NOW)
    assert r2.add(_obs("Regshield", ids[1], days=5)) is c
    assert c.status == RadarStatus.DISMISSED and len(c.mention_signal_ids) == 2


# ---------- score ----------


def _m(days, cred=1.0, impact=None):
    return Mention(NOW - timedelta(days=days), cred, impact)


def test_score_formula_and_breakdown():
    import math

    cfg = RadarScoreSettings()
    inp = ScoreInput(
        mentions=[_m(1, 1.0, 4), _m(2, 0.35, 2), _m(3, 0.35), _m(40, 0.5, 3)],
        source_credibility=[1.0, 0.35, 0.5],
        last_seen=NOW - timedelta(days=1),
        stars=Velocity(500, 20.0, "age", 25),
        hn_points_max=120,
    )
    score, b = compute(inp, cfg, NOW)
    c = b["components"]
    assert c["relevance"]["factor"] == pytest.approx(3 / 4, abs=1e-4)  # max impact 4
    assert c["relevance"]["value"] == {"max_impact": 4, "relation": None, "basis": "impact"}
    assert c["mentions"]["value"]["weighted"] == 2.2
    assert c["mentions"]["factor"] == pytest.approx(math.log1p(2.2) / math.log1p(6), abs=1e-4)
    assert c["source_diversity"]["factor"] == pytest.approx(1.85 / 2, abs=1e-4)
    star_f = math.log1p(20) / math.log1p(50)
    assert c["traction"]["factor"] == pytest.approx(star_f, abs=1e-4)
    assert c["recency"]["factor"] == pytest.approx(0.5 ** (1 / 14), abs=1e-4)
    expected = sum(100 * v["weight"] * v["factor"] for v in c.values())
    assert score == pytest.approx(expected, abs=0.1)
    assert b["formula"] == "radar_score.v2" and b["score"] == score
    assert set(c) == {
        "relevance",
        "mentions",
        "source_diversity",
        "mention_velocity",
        "traction",
        "recency",
    }


@pytest.mark.parametrize(
    ("relation", "expected"),
    [
        ("threatens", 0.7 * 1.0 + 0.3 * 0.25),
        ("complements", 0.7 * 0.6 + 0.3 * 0.25),
        ("none", 0.7 * 0.05 + 0.3 * 0.25),
        (None, 0.25),
    ],
)
def test_relevance_uses_profile_relation(relation, expected):
    _, b = compute(
        ScoreInput([_m(1, 1.0, 2)], [1.0], NOW, relation=relation), RadarScoreSettings(), NOW
    )
    assert b["components"]["relevance"]["factor"] == pytest.approx(expected, abs=1e-4)


def test_viral_hn_post_is_capped():
    _, b = compute(
        ScoreInput([_m(1, 0.35)], [0.35], NOW, hn_points_max=5000), RadarScoreSettings(), NOW
    )
    assert b["components"]["traction"]["factor"] == 0.5
    assert b["components"]["traction"]["points"] == 5.0


def test_press_mention_outweighs_discovery_mentions():
    cfg = RadarScoreSettings()
    _, press = compute(ScoreInput([_m(1, 1.0, 3)], [1.0], NOW), cfg, NOW)
    _, hn = compute(ScoreInput([_m(1, 0.35, 3)], [0.35], NOW), cfg, NOW)
    assert press["score"] > hn["score"] + 8


def test_one_new_mention_is_not_full_velocity():
    _, b = compute(ScoreInput([_m(1)], [1.0], NOW - timedelta(days=1)), RadarScoreSettings(), NOW)
    assert b["components"]["mention_velocity"]["factor"] == pytest.approx(1 / 3, abs=1e-4)
    assert b["components"]["relevance"]["factor"] == 0.0
    assert b["components"]["traction"]["factor"] == 0.0


def test_score_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1"):
        RadarScoreSettings(weights={"mentions": 0.5})


# ---------- enrich: discovery branch ----------


def test_untracked_drops_invented_urls():
    text = "Show HN: Regshield. Code at https://github.com/regshield/regshield."
    out = untracked(
        [
            {
                "name": "Regshield",
                "kind": "open_source_project",
                "context": "c",
                "url": "https://github.com/regshield/regshield/",
            },
            {"name": "Fakeco", "kind": "company", "context": "c", "url": "https://fakeco.example"},
            {"name": "Socket", "kind": "company", "context": "c", "url": None},
        ],
        {"socket"},
        text,
    )
    assert [e["name"] for e in out] == ["Regshield", "Fakeco"]
    assert out[0]["url"] == "https://github.com/regshield/regshield/"
    assert out[1]["url"] is None and out[1]["url_dropped"] == "https://fakeco.example"


DISC_TEXT = (
    "Show HN: Regshield blocks malicious packages. Code: https://github.com/regshield/regshield"
)


def _discovery_signal(s, src, title, *, kind="hn", text=DISC_TEXT, days=0, extra=None):
    sig = signal(s, src, title, text=text, days=days)
    meta = {"kind": "discovery", "discovery": kind, **(extra or {})}
    s.execute(update(Signal).where(Signal.id == sig.id).values(meta=meta))
    return sig


@pytest.fixture
def jfrog_world(collect_db):
    with collect_db.begin() as s:
        ensure_capability(s, "malicious-package-detection")
        jfrog = Entity(
            type="company",
            name="JFrog",
            slug="jfrog",
            relationship=Relationship.SELF,
            is_self=True,
            review_status="approved",
        )
        s.add(jfrog)
        s.flush()
        cur = entity(s, "jfrog-curation", "JFrog Curation", parent=jfrog)
        cur.relationship = Relationship.SELF
        cur.description = "Blocks malicious open-source packages."
        entity(s, "socket", "Socket")
    return collect_db


def test_enrich_uses_discovery_prompt_and_keeps_new_entities(jfrog_world):
    factory = jfrog_world
    with factory.begin() as s:
        hn = source(s, "HN", trust=0.4)
        sid = _discovery_signal(s, hn, "Show HN: Regshield").id
        s.execute(update(Signal).where(Signal.id == sid).values(relevant=True))

    def enrich(call):
        return {
            "category": "product_launch",
            "summary": "Regshield is an open-source registry firewall that blocks bad packages.",
            "why_jfrog_cares": "It overlaps with JFrog Curation, which blocks malicious packages.",
            "takeaway": "A new open-source alternative to JFrog Curation.",
            "impact": 3,
            "impact_rationale": "Level 3.",
            "evidence_quote": "Show HN: Regshield blocks malicious packages.",
            "is_claim": False,
            "entities": [],
            "capabilities": [],
            "new_entities": [
                {
                    "name": "Regshield",
                    "kind": "open_source_project",
                    "context": "Registry firewall.",
                    "url": "https://github.com/regshield/regshield",
                }
            ],
        }

    llm = FakeLLM(enrich_discovery=enrich)
    with factory.begin() as s:
        counts = enrich_signals(s, ctx(llm=llm, factory=factory))
    assert counts["enriched"] == 1
    assert [c.operation for c in llm.calls] == ["enrich_discovery"]
    assert llm.calls[0].prompt_version == "enrich_discovery.v1"
    with factory() as s:
        sig = s.get_one(Signal, sid)
        assert sig.enrich_prompt_version == "enrich_discovery.v1"
        [ne] = sig.meta["enrich"]["new_entities"]
        assert ne["kind"] == "open_source_project" and ne["url"].endswith("/regshield")
        # Enriched, but never visible in the news lenses (Brief, feed, synthesis).
        assert s.scalar(select(Signal.id).where(Signal.id == sid, visible())) is None


# ---------- radar step end to end ----------


def _profile_handler(*, bad_id=None, partial=()):
    def profile(call):
        from tests.pipeline.factories import signal_ids_in

        ids = signal_ids_in(call)
        first = ids[:1]
        return {
            "what_it_is": {
                "text": "Regshield is an open-source registry firewall.",
                "signal_ids": first,
            },
            "segments": {
                "text": "It filters packages from public registries.",
                "segments": ["supply_chain_security"],
                "signal_ids": first,
            },
            "jfrog_relation": {
                "text": "It blocks malicious packages like JFrog Curation does.",
                "relation": "threatens",
                "products": ["jfrog-curation"],
                "signal_ids": [bad_id] if bad_id else first,
            },
            "stage": {
                "text": "A young open-source project on GitHub.",
                "stage": "open_source_project",
                "signal_ids": first,
            },
            "why_notable_now": {
                "text": "It was presented on Hacker News this month.",
                "signal_ids": first,
            },
        }

    def verify(call):
        return {
            "items": [
                {"key": k, "verdict": "partial" if k in partial else "supported", "note": "n"}
                for k in claim_keys_in(call)
            ]
        }

    return profile, verify


def _radar_world(factory):
    with factory.begin() as s:
        hn = source(s, "HN", trust=0.4)
        gh = source(s, "GitHub search", trust=0.5)
        press = source(s, "Press", trust=0.7)
        triage = {"triage": {"segments": ["supply_chain_security"]}}
        ne = {
            "enrich": {
                "new_entities": [
                    {
                        "name": "Regshield",
                        "kind": "open_source_project",
                        "context": "Registry firewall.",
                        "url": "https://github.com/regshield/regshield",
                    }
                ]
            }
        }
        a = _discovery_signal(
            s, hn, "Show HN: Regshield", days=8, extra={**triage, **ne, "hn": {"points": 140}}
        )
        b = _discovery_signal(
            s,
            gh,
            "New GitHub repository: regshield/regshield",
            kind="github",
            days=7,
            extra={
                **triage,
                "repo": {
                    "full_name": "regshield/regshield",
                    "name": "regshield",
                    "homepage": "https://regshield.io",
                },
            },
        )
        c = signal(s, press, "Registry firewalls are back", days=9)
        s.execute(update(Signal).where(Signal.id == c.id).values(meta={**triage, **ne}))
        # Irrelevant items never produce candidates; tracked names are skipped.
        d = _discovery_signal(
            s,
            hn,
            "Show HN: my game",
            days=9,
            extra={
                "enrich": {"new_entities": [{"name": "Gamey", "kind": "product", "context": "x"}]}
            },
        )
        e = signal(s, press, "Socket news", days=9)
        s.execute(
            update(Signal)
            .where(Signal.id == e.id)
            .values(
                meta={
                    **triage,
                    "enrich": {
                        "new_entities": [{"name": "Socket", "kind": "company", "context": "x"}]
                    },
                }
            )
        )
        s.execute(
            update(Signal).where(Signal.id.in_([a.id, b.id, c.id, e.id])).values(relevant=True)
        )
        s.execute(update(Signal).where(Signal.id == d.id).values(relevant=False))
        s.add(
            RepoStarSnapshot(
                repo="regshield/regshield",
                captured_at=NOW,
                captured_day=NOW.date(),
                stars=800,
                repo_created_at=NOW - timedelta(days=20),
            )
        )
    return {"hn": a.id, "gh": b.id, "news": c.id}


def _step(factory, llm, force=frozenset()):
    c = ctx(llm=llm, factory=factory, force=force)
    c.now = NOW
    with factory.begin() as s:
        return radar_step(s, c, RadarSettings(profile={"min_score": 10, "top_n": 5}))


def test_radar_step_end_to_end(jfrog_world):
    factory = jfrog_world
    ids = _radar_world(factory)
    profile, verify = _profile_handler()
    llm = FakeLLM(radar_profile=profile, verify_profile=verify)
    counts = _step(factory, llm)
    assert counts["created"] == 1 and counts["skipped_tracked_name"] == 1
    with factory() as s:
        [cand] = s.scalars(select(RadarCandidate)).all()
        assert cand.name == "Regshield" and cand.kind == RadarKind.OPEN_SOURCE_PROJECT
        assert cand.mention_signal_ids == sorted(ids.values())
        assert cand.repo_url == "https://github.com/regshield/regshield"
        assert cand.homepage == "https://regshield.io"
        assert set(cand.meta["mention_origins"].values()) == {
            "hn_new_entity",
            "github_search",
            "news_new_entity",
        }
        assert cand.metrics["stars"] == 800 and cand.metrics["star_velocity_basis"] == "age"
        assert cand.metrics["hn_points_max"] == 140
        assert cand.score_breakdown["components"]["source_diversity"]["value"]["sources"] == 3
        assert cand.score_breakdown["formula"] == "radar_score.v2"
        assert cand.profile_status == ProfileStatus.OK
        assert set(cand.profile["items"]) == set(KEYS) and cand.profile["dropped"] == []
        assert cand.profile["items"]["jfrog_relation"]["products"] == ["jfrog-curation"]
        assert "excerpt" not in str(cand.profile)  # source text is not stored in the profile
    assert [c.operation for c in llm.calls] == ["radar_profile", "verify_profile"]
    assert llm.calls[0].task == "bulk" and llm.calls[1].task == "verify"

    # Second run: same mentions → no LLM calls, nothing duplicated.
    again = _step(factory, llm)
    assert again["profiles"].get("unchanged") == 1 and len(llm.calls) == 2
    assert again["matched_exact"] >= 1 and again.get("created", 0) == 0


def test_profile_invalid_citation_and_verifier_drops(jfrog_world):
    factory = jfrog_world
    _radar_world(factory)
    profile, verify = _profile_handler(bad_id=999999, partial=("stage",))
    llm = FakeLLM(radar_profile=profile, verify_profile=verify)
    _step(factory, llm)
    with factory() as s:
        cand = s.scalars(select(RadarCandidate)).one()
        assert cand.profile_status == ProfileStatus.OK
        dropped = {d["key"]: d for d in cand.profile["dropped"]}
        assert dropped["jfrog_relation"]["reason"] == "invalid_citation"
        assert dropped["jfrog_relation"]["invalid_ids"] == {"999999": "unknown"}
        assert dropped["stage"]["reason"] == "verifier_partial"
        assert set(cand.profile["items"]) == {"what_it_is", "segments", "why_notable_now"}


def test_profile_rejected_when_what_it_is_fails(jfrog_world):
    factory = jfrog_world
    _radar_world(factory)
    profile, verify = _profile_handler(partial=("what_it_is",))
    _step(factory, FakeLLM(radar_profile=profile, verify_profile=verify))
    with factory() as s:
        cand = s.scalars(select(RadarCandidate)).one()
        assert cand.profile_status == ProfileStatus.REJECTED


def test_observations_skip_irrelevant(jfrog_world):
    factory = jfrog_world
    _radar_world(factory)
    with factory() as s:
        names = sorted({o.name for o in observations(s)})
    assert names == ["Regshield", "Socket", "regshield"]


def test_collect_source_stores_star_snapshots_and_skips(collect_db):
    from app.pipeline.collect import collect_source
    from app.pipeline.collectors import CollectResult, SourceRef
    from app.pipeline.radar.stars import StarSnapshot

    with collect_db.begin() as s:
        src = source(s, "GitHub search")
        ref = SourceRef(
            id=src.id,
            name=src.name,
            url=src.url,
            fetch_method=FetchMethod.GITHUB_SEARCH,
            options={},
        )
        from app.db.models import PipelineRun

        run = PipelineRun(trigger="manual")
        s.add(run)
        s.flush()
        run_id = run.id

    class Fake:
        def collect(self, source, *, since, backfill, known):
            r = CollectResult()
            r.star_snapshots = [
                StarSnapshot("a/b", NOW, 10),
                StarSnapshot("a/b", NOW + timedelta(hours=1), 12),
            ]
            r.skip("fork")
            return r

    report = collect_source(collect_db, Fake(), ref, run_id=run_id, since=T0, backfill=False)
    assert report.star_snapshots == 1 and report.skipped == {"fork": 1}
    with collect_db() as s:
        [row] = s.scalars(select(RepoStarSnapshot)).all()
        assert (row.repo, row.stars, row.run_id) == ("a/b", 12, run_id)  # last capture of the day


def test_relation_none_needs_empty_products():
    from pydantic import ValidationError

    from app.pipeline.radar.profile import profile_schema

    schema = profile_schema(["jfrog-curation"])
    base = {
        "what_it_is": {"text": "A consumer email app.", "signal_ids": [1]},
        "segments": {
            "text": "Outside JFrog's market.",
            "segments": ["distribution"],
            "signal_ids": [1],
        },
        "stage": {"text": "A solo project.", "stage": "unknown", "signal_ids": [1]},
        "why_notable_now": {"text": "Shown on Hacker News.", "signal_ids": [1]},
    }
    rel = {"text": "It does not touch JFrog's products.", "signal_ids": [1]}
    ok = schema.model_validate(
        {**base, "jfrog_relation": {**rel, "relation": "none", "products": []}}
    )
    assert ok.jfrog_relation.relation == "none"
    with pytest.raises(ValidationError, match="leave products empty"):
        schema.model_validate(
            {**base, "jfrog_relation": {**rel, "relation": "none", "products": ["jfrog-curation"]}}
        )
    with pytest.raises(ValidationError, match="name the JFrog products"):
        schema.model_validate(
            {**base, "jfrog_relation": {**rel, "relation": "threatens", "products": []}}
        )


def test_profile_relation_from_kept_or_partial_only():
    from app.pipeline.radar.profile import profile_relation

    kept = {"items": {"jfrog_relation": {"relation": "threatens"}}, "dropped": []}
    partial = {
        "items": {},
        "dropped": [{"key": "jfrog_relation", "reason": "verifier_partial", "relation": "none"}],
    }
    bad = {
        "items": {},
        "dropped": [
            {"key": "jfrog_relation", "reason": "verifier_unsupported", "relation": "none"}
        ],
    }
    assert profile_relation(kept) == "threatens"
    assert profile_relation(partial) == "none"
    assert profile_relation(bad) is None and profile_relation(None) is None


def test_prune_removes_mentions_no_longer_observed(jfrog_world):
    factory = jfrog_world
    ids = _radar_world(factory)
    profile, verify = _profile_handler()
    _step(factory, FakeLLM(radar_profile=profile, verify_profile=verify))
    # The news article is re-triaged as not relevant: its mention disappears.
    with factory.begin() as s:
        s.execute(update(Signal).where(Signal.id == ids["news"]).values(relevant=False))
    counts = _step(factory, FakeLLM(radar_profile=profile, verify_profile=verify))
    assert counts["mentions_pruned"] == 1
    with factory() as s:
        cand = s.scalars(select(RadarCandidate)).one()
        assert ids["news"] not in cand.mention_signal_ids
        assert str(ids["news"]) not in cand.meta["mention_origins"]
        assert len(cand.source_ids) == 2


def test_new_candidate_without_mentions_is_removed(jfrog_world):
    factory = jfrog_world
    _radar_world(factory)
    profile, verify = _profile_handler()
    _step(factory, FakeLLM(radar_profile=profile, verify_profile=verify))
    with factory.begin() as s:
        s.execute(update(Signal).values(relevant=False))
    counts = _step(factory, FakeLLM(radar_profile=profile, verify_profile=verify))
    assert counts["candidates_removed"] == 1
    with factory() as s:
        assert s.scalars(select(RadarCandidate)).all() == []


def test_profile_relation_drives_the_score(jfrog_world):
    factory = jfrog_world
    _radar_world(factory)
    profile, verify = _profile_handler()

    def none_profile(call):
        out = profile(call)
        out["jfrog_relation"] = {**out["jfrog_relation"], "relation": "none", "products": []}
        return out

    _step(factory, FakeLLM(radar_profile=none_profile, verify_profile=verify))
    with factory() as s:
        cand = s.scalars(select(RadarCandidate)).one()
        rel = cand.score_breakdown["components"]["relevance"]
        assert rel["value"]["relation"] == "none" and rel["value"]["basis"] == "profile_relation"
        assert cand.metrics["relation"] == "none"


def test_triage_uses_discovery_prompt(jfrog_world):
    from app.pipeline.triage import triage_signals

    factory = jfrog_world
    with factory.begin() as s:
        hn = source(s, "HN", trust=0.4)
        press = source(s, "Press")
        d = _discovery_signal(s, hn, "Show HN: my email app").id
        n = signal(s, press, "News item").id
    llm = FakeLLM(triage_discovery={"relevant": False, "reason": "Email app.", "segments": []})
    with factory.begin() as s:
        triage_signals(s, ctx(llm=llm, factory=factory))
    ops = sorted(c.operation for c in llm.calls)
    assert ops == ["triage", "triage_discovery"]
    with factory() as s:
        assert s.get_one(Signal, d).meta["triage"]["prompt_version"] == "triage_discovery.v1"
        assert s.get_one(Signal, n).meta["triage"]["prompt_version"] == "triage.v1"
    # Current versions: nothing to redo.
    with factory.begin() as s:
        assert triage_signals(s, ctx(llm=llm, factory=factory))["selected"] == 0
