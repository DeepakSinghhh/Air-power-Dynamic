import json
import urllib.error

import pytest

from sarthi import candidates, greedy
from sarthi.copilot import LLMRouter, ask_schema, route, rules
from sarthi.scenario import generate


@pytest.fixture(scope="module")
def wp():
    w = generate(7)
    return w, greedy.solve(w, candidates.build(w))


@pytest.mark.parametrize("q, exp", [
    ("why isn't STK-06 planned?", {"intent": "why_not", "mission": "STK-06"}),
    ("What would it take to plan stk 6?", {"intent": "what_would_it_take", "mission": "STK-06"}),
    ("brief me on STK-01", {"intent": "brief", "mission": "STK-01"}),
    ("what if Halwara fogs in from 0500 to 0930", {"intent": "close_base", "base": "HLW", "start": 300, "end": 570}),
    ("close Ambala from 05:00 for 3 hours", {"intent": "close_base", "base": "AMB", "start": 300, "end": 480}),
    ("the runway at Sirsa was bombed", {"intent": "close_base", "base": "SRS", "reason": "runway cratered"}),
    ("what if HLW-SU30-02 goes u/s", {"intent": "aircraft_down", "tail": "HLW-SU30-02"}),
    ("what is HLW-SU30-02 doing today", {"intent": "aircraft", "tail": "HLW-SU30-02"}),
    ("raise STK-06 to P9", {"intent": "raise_priority", "mission": "STK-06", "priority": 9}),
    ("cancel STK-10", {"intent": "cancel", "mission": "STK-10"}),
    ("readiness at Jodhpur", {"intent": "readiness", "base": "JDH"}),
    ("how robust is the plan?", {"intent": "robustness"}),
    ("compare courses of action", {"intent": "coas"}),
    ("propose fog closures at 70%", {"intent": "fog_closures", "threshold": 0.7}),
    ("hold ground spares", {"intent": "spares"}),
    ("what is not planned?", {"intent": "unplanned"}),
    ("status", {"intent": "status"}),
])
def test_parser_routes(wp, q, exp):
    w, p = wp
    a = rules(q, w, p, {})
    assert a is not None and a.confident
    for k, v in exp.items():
        assert getattr(a, k) == v, (q, k, getattr(a, k))


def test_follow_up_uses_the_last_mission(wp):
    w, p = wp
    ctx = {"mission": "STK-06"}
    a = rules("what would it take?", w, p, ctx)
    assert a.intent == "what_would_it_take" and a.mission == "STK-06"


def _fake(replies):
    calls = []

    def post(url, body, timeout):
        calls.append((url, body))
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return post, calls


def test_llm_router_ollama_and_openai_with_grounding(wp):
    w, p = wp
    # Ollama: a schema-constrained choice; the parser's IDs win over the model's misreading.
    post, calls = _fake([{"message": {"content": json.dumps(
        {"intent": "aircraft_down", "mission": None, "base": None, "tail": "SRS-SU30-02", "threat": None,
         "start": None, "end": None, "priority": None})}}])
    llm = LLMRouter("http://x", "m", "ollama", post=post)
    a = llm.route("HLW-SU30-02 has a hydraulic leak", w, p)
    assert a.intent == "aircraft_down" and a.tail == "HLW-SU30-02"
    assert calls[0][0].endswith("/api/chat") and calls[0][1]["format"] == ask_schema(w)
    # OpenAI-compatible: json_schema rejected (llama-cpp-python answers 500) -> retried as json_object + schema.
    err = urllib.error.HTTPError("http://x", 500, "bad", {}, None)
    post, calls = _fake([err, {"choices": [{"message": {"content": json.dumps(
        {"intent": "why_not", "mission": "STK-06"})}}]}])
    a = LLMRouter("http://x/v1", "m", "openai", post=post).route("what's blocking STK-06?", w, p)
    assert a.intent == "why_not" and a.mission == "STK-06"
    assert calls[1][1]["response_format"]["type"] == "json_object"
    # Invented tools are rejected; an ID the question never mentions is dropped.
    post, _ = _fake([{"message": {"content": json.dumps({"intent": "launch_missiles"})}}])
    assert LLMRouter("http://x", "m", post=post).route("do it", w, p) is None
    post, _ = _fake([{"message": {"content": json.dumps({"intent": "brief", "mission": "STK-09"})}}])
    a = LLMRouter("http://x", "m", post=post).route("what's on HLW-SU30-09's schedule today?", w, p)
    assert a.intent == "aircraft" and a.tail == "HLW-SU30-09" and a.mission is None


def test_parser_first_model_only_as_fallback(wp):
    w, p = wp
    post, calls = _fake([{"message": {"content": json.dumps({"intent": "status"})}}])
    llm = LLMRouter("http://x", "m", post=post)
    a, how = route("why isn't STK-06 planned?", w, p, {}, llm)
    assert how == "rules" and not calls  # the parser was sure: no model call
    a, how = route("give me the big picture", w, p, {}, llm)
    assert how == "llm:m" and a.intent == "status"
    post, _ = _fake([OSError("model down")])
    a, how = route("how is STK-01 looking", w, p, {}, LLMRouter("http://x", "m", post=post))
    assert how == "rules" and a.intent == "brief"  # model unavailable: the parser's guess stands
