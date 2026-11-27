"""Explanations (ADR-020): templates, validation, and a fake language model.

No test talks to a real model or the network: the Ollama adapter is tested
with a patched ``urlopen``.
"""

import io
import json
import urllib.error
from collections.abc import Iterator
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from alpha.application.tracks import Workspace
from alpha.cli import EXIT_BAD_PARAMS, EXIT_OK, main
from alpha.explain import BOUNDARY, RewriteError, explain
from alpha.explain.validate import check
from alpha.llm import OllamaRewriter
from tests.integration.test_baseline import add

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(list(argv), out=out), out.getvalue()


@pytest.fixture
def data(tmp_path: Path) -> Iterator[str]:
    ws = Workspace(tmp_path)
    for i, speed in enumerate((1.0, 1.1, 1.2)):
        add(ws, f"h{i}", speed)
    ws.close()
    yield str(tmp_path)


def scenario_report(data: str) -> dict[str, Any]:
    code, text = run(
        "--data-dir", data, "scenarios", "1", "--break-min", "30", "--json"
    )
    assert code == EXIT_OK
    report: dict[str, Any] = json.loads(text)
    return report


# --- templates ----------------------------------------------------------------------


def test_template_states_only_report_values(data: str) -> None:
    report = scenario_report(data)
    e = explain("scenarios", report)
    assert e.source == "template" and e.boundary == BOUNDARY
    text = " ".join(s.text for s in e.statements)
    assert "DIN 33466 standard speeds" in text and "4.0 km/h" in text
    assert "with 0:30 h of breaks" in text.replace("With", "with")
    # The template passes its own validator.
    statements = [(s.text, s.facts) for s in e.statements]
    assert e.facts is not None and not check(statements, e.facts, "")
    for s in e.statements:
        assert all(k in e.facts for k in s.facts)


def test_baseline_template_without_a_fit(data: str) -> None:
    _code, text = run("--data-dir", data, "baseline", "--json")
    e = explain("baseline", json.loads(text))
    joined = " ".join(s.text for s in e.statements)
    assert "not enough yet" in joined and "Model 2" in joined


# --- validator --------------------------------------------------------------------


def test_validator_accepts_other_number_formats(data: str) -> None:
    e = explain("scenarios", scenario_report(data))
    assert e.facts is not None
    moving = e.facts["estimate.baseline.moving"]
    minutes = round(float(moving.value) / 60)
    h, mm = divmod(minutes, 60)
    variants = [
        f"You need about {h} h {mm} min of moving time.",
        f"That is {h}:{mm:02d} moving.",
        f"The total with breaks is {e.facts.show('estimate.baseline.total')}.",
    ]
    for text in variants:
        assert not check([(text, ())], e.facts, ""), text


def test_validator_rejects_invented_values_and_forbidden_topics(data: str) -> None:
    e = explain("scenarios", scenario_report(data))
    facts = e.facts
    assert facts is not None
    cases = {
        "It takes 9:41 h.": "duration",
        "You climb 1234 m.": "number 1234",
        "This route is safe for you.": "safe",
        "The weather will slow you down.": "weather",
        "You should go early.": "should go",
        "Avalanche risk is low.": "Avalanche",
    }
    for text, expected in cases.items():
        problems = check([(text, ())], facts, "")
        assert problems and expected.lower() in problems[0].message.lower(), text
    unknown = check([("Fine.", ("no.such.fact",))], facts, "")
    assert unknown[0].message == "cites unknown fact 'no.such.fact'"


# --- orchestration with a fake model ------------------------------------------------


def answer(statements: list[tuple[str, list[str]]]) -> str:
    return json.dumps({"statements": [{"text": t, "facts": k} for t, k in statements]})


def test_model_text_is_used_only_when_it_passes(data: str) -> None:
    report = scenario_report(data)
    draft = explain("scenarios", report)
    good = [(f"In other words: {s.text}", list(s.facts)) for s in draft.statements]

    def fake(system: str, user: str) -> str:
        assert "Do not mention safety" in system
        assert "estimate.baseline.moving =" in user  # facts are in the prompt
        return answer(good)

    used = explain("scenarios", report, fake, model="fake")
    assert used.source == "model" and used.model == "fake"
    assert used.statements[0].text.startswith("In other words")

    bad = list(good)
    bad[0] = ("You will need exactly 12:34 h.", [])
    fallback = explain("scenarios", report, lambda s, u: answer(bad), model="fake")
    assert fallback.source == "template"
    assert fallback.statements == draft.statements
    assert "statement 1: duration of 754 min" in fallback.fallback_reasons[0]

    unsafe = [("Your route is safe.", []), *good[1:]]
    assert (
        "safe"
        in explain("scenarios", report, lambda s, u: answer(unsafe)).fallback_reasons[0]
    )
    short = explain("scenarios", report, lambda s, u: answer(good[:1]))
    assert "dropped points" in short.fallback_reasons[0]
    garbage = explain("scenarios", report, lambda s, u: "not json")
    assert "not valid JSON" in garbage.fallback_reasons[0]

    def down(system: str, user: str) -> str:
        raise RewriteError("no local Ollama server")

    offline = explain("scenarios", report, down)
    assert (
        offline.source == "template" and "not available" in offline.fallback_reasons[0]
    )


def test_unknown_topic() -> None:
    with pytest.raises(ValueError, match="unknown topic"):
        explain("weather", {})


# --- Ollama adapter (patched urlopen, no network) -----------------------------------


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_ollama_adapter_and_cpu_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, Any]] = []

    def urlopen(request: Any, timeout: float) -> FakeResponse:
        body = json.loads(request.data)
        bodies.append(body)
        assert request.full_url == "http://127.0.0.1:11434/api/chat"
        if "num_gpu" not in body["options"]:
            raise urllib.error.HTTPError(
                request.full_url,
                500,
                "error",
                Message(),
                io.BytesIO(b'{"error":"CUDA error"}'),
            )
        content = json.dumps({"statements": [{"text": "ok", "facts": []}]})
        return FakeResponse(json.dumps({"message": {"content": content}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    rewriter = OllamaRewriter("llama3.1:8b")
    assert json.loads(rewriter("sys", "user"))["statements"][0]["text"] == "ok"
    assert len(bodies) == 2 and bodies[1]["options"]["num_gpu"] == 0
    assert bodies[0]["format"]["required"] == ["statements"]
    assert bodies[0]["keep_alive"] == "15m"  # model stays loaded between requests
    assert "CPU" in rewriter.notes[0]

    def refused(request: Any, timeout: float) -> FakeResponse:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", refused)
    with pytest.raises(RewriteError, match="start Ollama"):
        OllamaRewriter()("sys", "user")

    def missing(request: Any, timeout: float) -> FakeResponse:
        raise urllib.error.HTTPError(
            request.full_url, 404, "nf", Message(), io.BytesIO(b"{}")
        )

    monkeypatch.setattr("urllib.request.urlopen", missing)
    with pytest.raises(RewriteError, match="ollama pull"):
        OllamaRewriter("tiny")("sys", "user")


# --- CLI ------------------------------------------------------------------------------


def test_cli_explain(data: str, monkeypatch: pytest.MonkeyPatch) -> None:
    code, text = run("--data-dir", data, "explain", "scenarios", "1")
    assert code == EXIT_OK
    assert "(Explained by ALPHA's templates.)" in text and "does not judge" in text
    assert run("--data-dir", data, "explain", "scenarios")[0] == EXIT_BAD_PARAMS
    code, text = run("--data-dir", data, "explain", "baseline", "--json")
    report = json.loads(text)
    assert report["topic"] == "baseline" and report["source"] == "template"
    assert report["facts"] and report["boundary"] == BOUNDARY

    def refused(request: Any, timeout: float) -> FakeResponse:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", refused)
    code, text = run("--data-dir", data, "explain", "scenarios", "1", "--llm")
    assert code == EXIT_OK and "The model's text was not used" in text


def test_caveats_are_never_sent_to_the_model_and_always_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alpha.explain import TOPICS, FactSet, Statement

    def topic(report: dict[str, Any]) -> tuple[FactSet, tuple[Statement, ...]]:
        facts = FactSet()
        facts.add("slope.median", "median slope", 20.0, "deg", "calculated")
        return facts, (
            Statement("The median slope is 20 deg.", ("slope.median",)),
            Statement("It is not a hazard rating.", (), caveat=True),
        )

    monkeypatch.setitem(TOPICS, "test", topic)
    seen: list[str] = []

    def fake(system: str, user: str) -> str:
        seen.append(user)
        return answer(
            [("Typically the ground is about 20 deg steep.", ["slope.median"])]
        )

    e = explain("test", {}, fake)
    assert "hazard" not in seen[0]  # the caveat is not in the prompt
    assert e.source == "model"
    assert [s.text for s in e.statements] == [
        "Typically the ground is about 20 deg steep.",
        "It is not a hazard rating.",
    ]

    def split(system: str, user: str) -> str:
        return answer(
            [("The ground is", []), ("about 20 deg steep.", ["slope.median"])]
        )

    e = explain("test", {}, split)  # more statements: caveats go last
    assert e.statements[-1].text == "It is not a hazard rating."


# --- API --------------------------------------------------------------------------


def test_api_explanations(data: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from alpha.api import create_app

    with TestClient(create_app(Path(data), llm_model="tiny:1b")) as client:
        plain = client.get(
            "/tracks/1/explanation", params={"topic": "scenarios", "break_min": 30}
        )
        assert plain.status_code == 200, plain.text
        body = plain.json()
        assert body["source"] == "template" and body["model"] is None
        assert all("caveat" in st for st in body["statements"])
        assert "does not judge" in body["boundary"]

        missing = client.get("/tracks/1/explanation", params={"topic": "elevation"})
        assert (missing.status_code, missing.json()["error"]["code"]) == (
            409,
            "DEM_PROFILE_MISSING",
        )
        personal = client.get(
            "/tracks/1/explanation", params={"topic": "scenarios", "personal": True}
        )
        assert personal.json()["error"]["code"] == "BASELINE_NOT_FITTED"
        bad = client.get("/tracks/1/explanation", params={"topic": "weather"})
        assert bad.status_code == 422
        assert client.get("/baseline/explanation").json()["topic"] == "baseline"

        requests: list[dict[str, Any]] = []

        def urlopen(request: Any, timeout: float) -> FakeResponse:
            body = json.loads(request.data)
            requests.append(body)
            draft = body["messages"][1]["content"]
            # Echo the draft sentences and their citations back.
            items = []
            for ln in draft.split("Draft:\n", 1)[1].strip().splitlines():
                text, cited = ln.split(". ", 1)[1].rsplit(" [facts: ", 1)
                keys = [k for k in cited.rstrip("]").split(", ") if k != "none"]
                items.append({"text": text, "facts": keys})
            content = json.dumps({"statements": items})
            return FakeResponse(json.dumps({"message": {"content": content}}).encode())

        monkeypatch.setattr("urllib.request.urlopen", urlopen)
        worded = client.get(
            "/tracks/1/explanation",
            params={"topic": "scenarios", "llm": True, "language": "German"},
        ).json()
        assert worded["source"] == "model" and worded["model"] == "tiny:1b"
        assert requests[0]["model"] == "tiny:1b"
        assert "Language of the answer: German" in requests[0]["messages"][1]["content"]

        def refused(request: Any, timeout: float) -> FakeResponse:
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", refused)
        down = client.get(
            "/tracks/1/explanation", params={"topic": "scenarios", "llm": True}
        ).json()
        assert (
            down["source"] == "template"
            and "start Ollama" in down["fallback_reasons"][0]
        )


def test_rounding_is_accepted_but_other_values_are_not() -> None:
    from alpha.explain import FactSet

    facts = FactSet()
    facts.add("k.low", "lower end", 1.123, "factor", "fitted")  # shown as x1.12
    facts.add("dist", "distance", 8331.1, "km", "calculated")  # shown as 8.33 km
    facts.add("err", "typical error", 0.0253, "percent", "fitted")  # shown as 3 %
    ok = [
        "A hike may be 12 % faster than standard.",  # 12.3 % rounded
        "Your lower end is x1.12.",
        "It is 8.3 km long.",
        "It is 8.33 km long.",
        "The error was about 3 %.",
        "The error was 2.5 %.",
    ]
    for text in ok:
        assert not check([(text, ())], facts, ""), text
    wrong = ["A hike may be 13 % faster.", "It is 8.4 km long.", "Off by 4 %.", "x1.13"]
    for text in wrong:
        assert check([(text, ())], facts, ""), text


def test_a_dropped_value_falls_back_to_the_template(data: str) -> None:
    report = scenario_report(data)
    draft = explain("scenarios", report)
    assert draft.facts is not None
    moving = draft.facts.show("estimate.baseline.moving")
    # Every statement kept, but the moving time is left out of the text.
    worded = [
        (s.text.replace(moving, "a while"), list(s.facts)) for s in draft.statements
    ]
    e = explain("scenarios", report, lambda sy, us: answer(worded))
    assert e.source == "template"
    assert "moving time" in e.fallback_reasons[0]  # cited but not stated


def test_a_right_number_with_the_wrong_meaning_is_rejected(data: str) -> None:
    from alpha.explain.validate import citation_problems

    report = scenario_report(data)
    draft = explain("scenarios", report)
    facts = draft.facts
    assert facts is not None
    moving = facts.show("estimate.baseline.moving")
    climbing = facts.show("estimate.baseline.vertical")
    # The climbing time presented as the moving time, citing the moving time.
    wrong = [
        (
            s.text.replace(moving, climbing)
            if "estimate.baseline.moving" in s.facts
            else s.text,
            list(s.facts),
        )
        for s in draft.statements
    ]
    e = explain("scenarios", report, lambda sy, us: answer(wrong))
    assert e.source == "template"
    joined = " ".join(e.fallback_reasons)
    assert "cites the moving time but does not state it" in joined

    # Every template passes the citation rules itself.
    statements = [(s.text, s.facts) for s in draft.statements]
    assert not citation_problems(statements, facts, statements)
    _code, text = run("--data-dir", data, "baseline", "--json")
    base = explain("baseline", json.loads(text))
    assert base.facts is not None
    pairs = [(s.text, s.facts) for s in base.statements]
    assert not citation_problems(pairs, base.facts, pairs)


def test_cpu_fallback_is_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, Any]] = []
    failures = iter([True, False])  # the GPU fails once, then works

    def urlopen(request: Any, timeout: float) -> FakeResponse:
        body = json.loads(request.data)
        bodies.append(body)
        if "num_gpu" not in body["options"] and next(failures):
            raise urllib.error.HTTPError(
                request.full_url, 500, "error", Message(), io.BytesIO(b"CUDA error")
            )
        content = json.dumps({"statements": [{"text": "ok", "facts": []}]})
        return FakeResponse(json.dumps({"message": {"content": content}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    rewriter = OllamaRewriter()
    rewriter("sys", "first")  # GPU fails -> CPU
    rewriter("sys", "second")  # tries the GPU again
    assert [("num_gpu" in b["options"]) for b in bodies] == [False, True, False]
    assert len(rewriter.notes) == 1


# --- comparison -------------------------------------------------------------------


def test_cli_explains_a_comparison(data: str) -> None:
    code, text = run("--data-dir", data, "explain", "comparison", "1", "2")
    assert code == EXIT_OK
    assert "This compares track B (#2) against track A (#1)." in text
    assert "say nothing about conditions" in text  # ALPHA's note, as a caveat
    code, text = run("--data-dir", data, "explain", "comparison", "1")
    assert code == EXIT_BAD_PARAMS and "two track IDs" in text
    code, _ = run("--data-dir", data, "explain", "baseline", "1")
    assert code == EXIT_BAD_PARAMS


def test_api_explains_a_comparison(data: str) -> None:
    from fastapi.testclient import TestClient

    from alpha.api import create_app

    with TestClient(create_app(Path(data))) as client:
        r = client.get("/comparisons/explanation", params={"a": 1, "b": 2})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["topic"] == "comparison" and body["source"] == "template"
        assert any(s["caveat"] for s in body["statements"])
        missing = client.get("/comparisons/explanation", params={"a": 1, "b": 99})
        assert missing.status_code == 404
