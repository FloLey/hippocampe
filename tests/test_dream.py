"""Dream pipeline: parsing, index, model choice, execution, migration.

Internals are tested through their own submodule (``pipeline``, ``models`` ...);
the package root re-exports only the public surface.
"""

import datetime

from wiki_server import dream
from wiki_server.dream import index, migration, models, pipeline, runner, usage


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# --- pure helpers ---------------------------------------------------------

def test_parse_json_plain():
    assert models._parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_in_code_fence():
    assert models._parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_garbage():
    assert models._parse_json("not json") is None


def test_as_list_normalizes():
    assert pipeline._as_list(None) == []
    assert pipeline._as_list({"x": 1}) == [{"x": 1}]
    assert pipeline._as_list([1, 2]) == [1, 2]


def test_render_index_groups_known_then_legacy():
    paths = {
        "long_term/self/identity.md",
        "long_term/people/margaux.md",
        "long_term/entities/old.md",  # legacy folder, not a known category
    }
    descs = {"long_term/people/margaux.md": "soeur"}
    out = index._render_index(paths, descs)
    assert "## people" in out and "people/margaux.md" in out and "soeur" in out
    # Known categories come first, the leftover legacy folder is appended.
    assert out.index("## people") < out.index("## entities")


# --- per-stage cost -------------------------------------------------------

def test_usage_tracks_cost_per_stage():
    u = models._Usage()
    u.add("claude-haiku-4-5-20251001", 1_000_000, 0, "triage")  # 1$ in
    u.add("claude-opus-4-8", 0, 1_000_000, "write")             # 25$ out
    e = u.entry(datetime.datetime(2026, 6, 4))
    assert e["by_stage"]["triage"]["cost"] == 1.0
    assert e["by_stage"]["write"]["cost"] == 25.0
    assert e["cost"] == 26.0


def test_cost_lines_shows_model_and_price():
    u = models._Usage()
    u.add("claude-haiku-4-5-20251001", 1_000_000, 0, "triage")
    lines = u.cost_lines()
    assert len(lines) == 1
    assert "triage" in lines[0] and "haiku" in lines[0] and "$1.00" in lines[0]


def test_usage_summary_aggregates_stages(wiki, write):
    import json
    write("dream_reports/usage.json", json.dumps([
        {"cost": 2.0, "by_stage": {"triage": {"input_tokens": 1, "output_tokens": 0, "cost": 1.0},
                                   "decide": {"input_tokens": 0, "output_tokens": 2, "cost": 1.0}}},
        {"cost": 1.0, "by_stage": {"triage": {"input_tokens": 3, "output_tokens": 0, "cost": 1.0}}},
    ]))
    s = usage.usage_summary()
    assert s["by_stage"]["triage"]["cost"] == 2.0
    assert s["by_stage"]["triage"]["input_tokens"] == 4
    assert s["by_stage"]["decide"]["cost"] == 1.0


# --- model selection ------------------------------------------------------

def test_model_default_is_opus(wiki):
    assert dream.effective_models() == {s: dream.DEFAULT_MODEL for s in dream.STAGES}


def test_set_models_validates_and_persists(wiki):
    stored = dream.set_models(
        {"triage": "claude-haiku-4-5-20251001", "decide": "claude-sonnet-4-6", "write": "bogus"}
    )
    assert stored == {"triage": "claude-haiku-4-5-20251001", "decide": "claude-sonnet-4-6"}
    eff = dream.effective_models()
    assert eff["triage"] == "claude-haiku-4-5-20251001"
    assert eff["write"] == dream.DEFAULT_MODEL  # unset -> default


def test_ui_model_wins_over_env(wiki, monkeypatch):
    dream.set_models({"triage": "claude-haiku-4-5-20251001"})
    monkeypatch.setenv("WIKI_DREAM_MODEL_TRIAGE", "claude-opus-4-8")
    assert models._model_for("triage") == "claude-haiku-4-5-20251001"


# --- execution ------------------------------------------------------------

def _stm(write, *names):
    write("short_term/index.md", "# STM\n")
    write("long_term/index.md", "# LTM\n")
    for n in names:
        write(f"short_term/entries/{n}.md", f"---\ncreated: 2026-06-04\n---\n\n{n}\n")


def test_execute_cumulative_merge(wiki, write, monkeypatch):
    _stm(write, "a", "b")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {"pages": [
            {"action": "promote", "page": "long_term/people/margaux.md", "change": "soeur"}]}},
        {"unit": {"stm": ["b.md"]}, "decision": {"pages": [
            {"action": "promote", "page": "long_term/people/margaux.md", "change": "mere"}]}},
    ])
    # The write stage merges onto whatever it receives as current content.
    monkeypatch.setattr(pipeline, "_write_page",
                        lambda u, p, op, current="", **kw: {"content": (current or "# p\n") + f"\n- {op['change']}", "description": "d"})
    _, report = pipeline._execute(_now(), "2026-06-04")
    page = (wiki / "long_term/people/margaux.md").read_text()
    assert "soeur" in page and "mere" in page  # neither contribution overwritten
    # Report has the plan and a deduped applied summary: one page line, listed once.
    assert "## Plan" in report and "## Appliqué" in report
    assert report.count("long_term/people/margaux.md : touchée par 2") == 1
    assert "1 page(s) écrite(s) : long_term/people/margaux.md" in report


def test_execute_writes_each_page_once(wiki, write, monkeypatch):
    _stm(write, "a", "b", "c")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": [f"{n}.md"]}, "decision": {"pages": [
            {"action": "promote", "page": "long_term/self/identity.md", "change": n}]}}
        for n in ("a", "b", "c")
    ])
    calls = {"n": 0}

    def counting_write(u, p, op, current="", **kw):
        calls["n"] += 1
        return {"content": (current or "# id\n") + op["change"], "description": "d"}

    monkeypatch.setattr(pipeline, "_write_page", counting_write)
    pipeline._execute(_now(), "2026-06-04")
    # Three units touch the same page, but it is written only once.
    assert calls["n"] == 1
    page = (wiki / "long_term/self/identity.md").read_text()
    assert "a" in page and "b" in page and "c" in page


def test_normalize_page_adds_long_term_prefix():
    assert pipeline._normalize_page("people/nina.md") == "long_term/people/nina.md"
    assert pipeline._normalize_page("long_term/self/identity.md") == "long_term/self/identity.md"
    assert pipeline._normalize_page("/projects/x.md") == "long_term/projects/x.md"
    assert pipeline._normalize_page("/long_term/people/nina.md") == "long_term/people/nina.md"
    assert pipeline._normalize_page("not a path") is None
    assert pipeline._normalize_page(None) is None


def test_execute_applies_category_relative_path(wiki, write, monkeypatch):
    _stm(write, "a")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {"pages": [
            {"action": "promote", "page": "people/nina.md", "change": "copine"}]}},
    ])
    monkeypatch.setattr(pipeline, "_write_page",
                        lambda u, p, op, current="", **kw: {"content": "# Nina\n", "description": "d"})
    pipeline._execute(_now(), "2026-06-04")
    # The prefix-less path is applied under long_term/, not rejected.
    assert (wiki / "long_term/people/nina.md").exists()
    assert not (wiki / "short_term/entries/a.md").exists()  # STM consumed (it worked)


def test_execute_atomic_keeps_stm_on_failure(wiki, write, monkeypatch):
    _stm(write, "a")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {
            "pages": [{"action": "promote", "page": "long_term/people/ok.md", "change": "x"},
                      {"action": "integrate", "page": "long_term/private/secret.md", "change": "y"}],
            "temporal": [{"type": "event", "due": "2026-07-01", "content": "evt"}]}},
    ])
    monkeypatch.setattr(pipeline, "_write_page",
                        lambda u, p, op, current="", **kw: {"content": "# x\n", "description": "d"})
    pipeline._execute(_now(), "2026-06-04")
    assert (wiki / "long_term/people/ok.md").exists()       # valid output applied
    assert not (wiki / "long_term/private/secret.md").exists()  # forbidden skipped
    assert (wiki / "short_term/entries/a.md").exists()      # STM kept (a part failed)


def test_execute_skips_dateless_temporal(wiki, write, monkeypatch):
    _stm(write, "a")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {
            "pages": [], "temporal": [{"type": "event", "due": None, "content": "naissance"}]}},
    ])
    pipeline._execute(_now(), "2026-06-04")
    assert list((wiki / "temporal").glob("*.md")) == []     # no never-expiring item
    assert (wiki / "short_term/entries/a.md").exists()      # nothing produced -> STM kept


# --- migration ------------------------------------------------------------

def test_migrate_entities(wiki, write, monkeypatch):
    write("long_term/index.md", "# LTM\n")
    write("long_term/entities/margaux.md", "---\ndescription: soeur\n---\n\n# Margaux\n\n[A](../entities/hugo.md)\n")
    write("long_term/entities/hugo.md", "---\ndescription: bebe\n---\n\n# Hugo\n")
    write("long_term/entities/dnb.md", "---\ndescription: emp\n---\n\n# DnB\n")
    monkeypatch.setattr(migration, "_classify_entities",
                        lambda usage, items: {"margaux": "people", "hugo": "people", "dnb": "organizations"})
    migration._migrate(_now(), "2026-06-04")
    assert (wiki / "long_term/people/margaux.md").exists()
    assert (wiki / "long_term/organizations/dnb.md").exists()
    assert not (wiki / "long_term/entities/margaux.md").exists()
    # link rewritten to the new folder
    assert "../people/hugo.md" in (wiki / "long_term/people/margaux.md").read_text()
    idx = (wiki / "long_term/index.md").read_text()
    assert "## people" in idx and "## organizations" in idx and "## entities" not in idx


# --- guarded wrapper ------------------------------------------------------

def test_guarded_reports_missing_key(wiki, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rel, report = dream.run_dry_run()
    assert "ANTHROPIC_API_KEY" in report
    assert rel.startswith("dream_reports/")


def test_guarded_turns_exception_into_report(wiki, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    def boom(when, day):
        raise RuntimeError("kaboom")
    rel, report = runner._guarded(False, boom)
    assert "kaboom" in report  # traceback captured, no raw 500


# --- entity resolution and temporal awareness -------------------------------

def _capture_stages(monkeypatch, triage_out, decide_out=None):
    """Stub the model: triage returns ``triage_out``, decide ``decide_out``;
    every stage's context is recorded for inspection."""
    seen = {"triage": [], "decide": [], "write": []}

    def fake_stage(usage, stage, context):
        seen[stage].append(context)
        if stage == "triage":
            return triage_out
        if stage == "decide":
            return decide_out or {"pages": [], "temporal": [], "rationale": "r"}
        return None

    monkeypatch.setattr(pipeline, "_stage", fake_stage)
    return seen


def test_triage_touches_without_prefix_reach_decide(wiki, write, monkeypatch):
    # The index lists people/nina.md; triage echoing that form must not drop it.
    _stm(write, "a")
    write("long_term/people/nina.md", "# Nina\n\nCopine de Valentin, infirmière.\n")
    seen = _capture_stages(monkeypatch, {"units": [
        {"stm": ["a.md"], "intent": "nina", "touches": ["people/nina.md"], "hint": "integrate"}]})
    pairs = pipeline._decisions(models._Usage(), "policy", [("a.md", "Nina tricote")], [])
    assert pairs[0]["unit"]["touches"] == ["long_term/people/nina.md"]
    assert "infirmière" in seen["decide"][0]           # the page content was shown
    assert "(aucune page touchée)" not in seen["decide"][0]


def test_decide_sees_code_found_related_pages(wiki, write, monkeypatch):
    _stm(write, "a")
    write("long_term/people/nina-durand.md", "# Nina Durand\n\nÉtat civil.\n")
    write("long_term/projects/boisflotte.md", "# BoisFlotté\n")
    seen = _capture_stages(monkeypatch, {"units": [
        {"stm": ["a.md"], "intent": "x", "touches": [], "hint": "promote"}]})
    pipeline._decisions(models._Usage(), "policy", [("a.md", "Soirée avec Nina")], [])
    related = seen["decide"][0].split("<related_pages>")[1]
    assert "long_term/people/nina-durand.md" in related
    assert "boisflotte" not in related


def test_related_pages_ignores_generic_tokens(wiki, write):
    for name in ("voyage-japon", "voyage-islande", "voyage-porto", "voyage-biarritz",
                 "voyage-lisbonne", "voyage-rome"):
        write(f"long_term/places/{name}.md", "x")
    pages = pipeline._ltm_pages()
    # "voyage" names six pages: too generic to point at any one of them.
    assert pipeline._related_pages("un voyage", pages) == []
    assert pipeline._related_pages("retour de Islande", pages) == ["long_term/places/voyage-islande.md"]


def test_triage_and_decide_see_active_temporal(wiki, write, monkeypatch):
    _stm(write, "a")
    write("temporal/2026-12-31-derm.md",
          "---\ntype: todo\nstatus: active\ndue: 2026-12-31\n---\n\nRenouveler le passeport\n")
    seen = _capture_stages(monkeypatch, {"units": [
        {"stm": ["a.md"], "intent": "x", "touches": [], "hint": "temporal"}]})
    pipeline._decisions(models._Usage(), "policy", [("a.md", "fait")], [])
    assert "temporal/2026-12-31-derm.md" in seen["triage"][0]
    assert "temporal/2026-12-31-derm.md" in seen["decide"][0]


def test_execute_closes_temporal_done(wiki, write, monkeypatch):
    _stm(write, "a")
    write("temporal/2026-06-05-polyfract.md",
          "---\ntype: todo\ncreated: 2026-06-01\nstatus: active\ndue: 2026-06-05\n---\n\nClarifier Polyfract\n")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {
            "pages": [], "temporal": [], "temporal_done": ["2026-06-05-polyfract.md"]}}])
    _, report = pipeline._execute(_now(), "2026-10-17")
    assert "status: done" in (wiki / "temporal/2026-06-05-polyfract.md").read_text()
    assert not (wiki / "short_term/entries/a.md").exists()  # closing counts as done work
    assert "clôturé" in report


def test_execute_ignores_unknown_temporal_done(wiki, write, monkeypatch):
    _stm(write, "a")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {"temporal_done": ["temporal/nope.md"]}}])
    _, report = pipeline._execute(_now(), "2026-10-17")
    assert "Clôture ignorée" in report
    assert (wiki / "short_term/entries/a.md").exists()       # nothing done -> kept


def test_execute_refuses_lossy_rewrite(wiki, write, monkeypatch):
    _stm(write, "a")
    write("long_term/people/nina.md", "# Nina\n\n" + "fait important\n" * 50)
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {"pages": [
            {"action": "integrate", "page": "long_term/people/nina.md", "change": "x"}]}}])
    monkeypatch.setattr(pipeline, "_write_page",
                        lambda u, p, op, current="", **kw: {"content": "# Nina\n\nx\n", "description": "d"})
    _, report = pipeline._execute(_now(), "2026-10-17")
    assert (wiki / "long_term/people/nina.md").read_text().count("fait important") == 50
    assert "Réécriture refusée" in report
    assert (wiki / "short_term/entries/a.md").exists()       # kept for next time


def test_write_receives_source_entries(wiki, write, monkeypatch):
    _stm(write, "a")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {"pages": [
            {"action": "promote", "page": "long_term/places/x.md", "change": "résumé"}]}}])
    got = {}

    def fake_write(u, p, op, current="", sources=""):
        got["sources"] = sources
        return {"content": "# X\n", "description": "d"}

    monkeypatch.setattr(pipeline, "_write_page", fake_write)
    pipeline._execute(_now(), "2026-10-17")
    assert "### a.md" in got["sources"]


def test_execute_flags_secret_in_written_page(wiki, write, monkeypatch):
    _stm(write, "a")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {"pages": [
            {"action": "promote", "page": "long_term/people/x.md", "change": "c"}]}}])
    monkeypatch.setattr(pipeline, "_write_page", lambda u, p, op, current="", **kw: {
        "content": "# X\n\nRN : 90.02.03-123.45\n", "description": "d"})
    _, report = pipeline._execute(_now(), "2026-10-17")
    assert "secret probable" in report


# --- model layer ------------------------------------------------------------

def test_prices_for_current_models():
    assert models._prices_for("claude-haiku-5-5") == (0.10, 0.50)
    assert models._prices_for("claude-sonnet-5-5") == (2.0, 10.0)
    assert models._prices_for("claude-haiku-4-5-20251001") == (1.0, 5.0)
    assert models._prices_for("some-opus-like") == (5.0, 25.0)   # tier fallback


class _FakeMessage:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.stop_reason = stop_reason
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()


def _fake_client(monkeypatch, responses):
    """Replace the streamed call: each call pops the next response (a message,
    or an exception to raise) and records the request params."""
    import anthropic

    calls = []

    def fake_stream(client, params):
        calls.append(params)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(models, "_stream", fake_stream)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: object())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    return calls


def test_call_model_uses_structured_output(monkeypatch):
    calls = _fake_client(monkeypatch, [_FakeMessage('{"a": 1}')])
    out = models._call_model(models._Usage(), "claude-sonnet-5-5", "p", 100, "decide", {"type": "object"})
    assert out == '{"a": 1}'
    assert calls[0]["output_config"]["format"]["type"] == "json_schema"


def test_call_model_falls_back_without_schema(monkeypatch):
    import anthropic

    rejected = anthropic.BadRequestError.__new__(anthropic.BadRequestError)
    Exception.__init__(rejected, "schema not supported")
    calls = _fake_client(monkeypatch, [rejected, _FakeMessage('{"a": 1}')])
    out = models._call_model(models._Usage(), "claude-haiku-4-5", "p", 100, "triage", {"type": "object"})
    assert out == '{"a": 1}'
    assert "output_config" not in calls[1]


def test_call_model_discards_truncated_output(monkeypatch):
    _fake_client(monkeypatch, [_FakeMessage('{"content": "trunc', stop_reason="max_tokens")])
    u = models._Usage()
    assert models._call_model(u, "claude-sonnet-5-5", "p", 100, "write") is None
    assert u.out_tok == 5                                  # still billed, still counted


def test_json_schemas_are_strict():
    from wiki_server import prompts

    def check(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        if node.get("type") == "array":
            check(node["items"])

    for schema in prompts.JSON_SCHEMAS.values():
        check(schema)


def test_report_day_is_local(wiki):
    # 23:30 UTC on Oct 16 is already Oct 17 in Brussels (the default zone).
    when = datetime.datetime(2026, 10, 16, 23, 30, tzinfo=datetime.timezone.utc)
    assert runner._local_day(when) == "2026-10-17"


def test_duplicate_groups_same_category_only():
    pages = ["long_term/people/nina.md", "long_term/people/nina-durand.md",
             "long_term/projects/projet-led-crochet-nina.md",
             "long_term/concepts/levain.md", "long_term/concepts/levain-f2.md",
             "long_term/projects/levain-f2.md", "long_term/people/thomas.md"]
    groups = pipeline._duplicate_groups(pages)
    assert ["long_term/people/nina-durand.md", "long_term/people/nina.md"] in groups
    assert ["long_term/concepts/levain-f2.md", "long_term/concepts/levain.md"] in groups
    assert not any("thomas" in p for g in groups for p in g)


def test_reports_carry_health_section(wiki, write, monkeypatch):
    _stm(write, "a")
    write("long_term/self/identity.md", "# Moi\n\nFileVault ABCD-EFGH-JKLM-NPQR-STUV-WXYZ\n")
    write("long_term/people/nina.md", "x")
    write("long_term/people/nina-durand.md", "x")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [])
    _, report = pipeline._execute(_now(), "2026-10-17")
    assert "## Santé de la mémoire" in report
    assert "Taille de prime" in report
    assert "people/nina.md" in report.split("Doublons probables")[1]
    assert "Secret probable dans long_term/self/identity.md" in report


def test_closing_an_already_closed_item_consumes_entry(wiki, write, monkeypatch):
    _stm(write, "a")
    write("temporal/t.md", "---\ntype: todo\nstatus: done\ndue: 2026-06-05\n---\n\nT\n")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {"temporal_done": ["temporal/t.md"]}}])
    pipeline._execute(_now(), "2026-10-17")
    assert not (wiki / "short_term/entries/a.md").exists()
