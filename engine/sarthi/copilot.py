"""Copilot: questions in plain language, answered only through the engine.

Routing. A deterministic parser places most questions with no model at all, so the copilot works on an
air-gapped laptop. If it cannot, an optional local open-weight LLM (Ollama, or any OpenAI-compatible server
such as llama.cpp) maps the question to ONE tool and its arguments, under a JSON schema whose enums are the
real mission, base, aircraft and threat IDs: it can only choose, not invent.

Answers. The model never writes the answer. Every answer is a template over engine output, so every number
comes from the engine. Tools only read state or create a proposal; nothing changes until a human approves it.
Every question, route and tool call is logged (see api.py).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable, Protocol

from pydantic import BaseModel, Field

from . import met, robust, whatif
from .events import AircraftDown, BaseClosure, CancelMission, PriorityChange
from .geo import fmt_time, haversine_km
from .kpi import kpis
from .models import Plan, World
from .readiness import readiness
from .threats import effective_envelope

TOOLS: dict[str, str] = {
    "help": "what the copilot can do; anything not about the plan",
    "status": "the overall picture: missions planned, fulfilment, expected value, sitrep",
    "unplanned": "which missions are not planned (no mission named)",
    "why_not": "why one named mission is not planned, what blocks it",
    "what_would_it_take": "how to get one named unplanned mission planned, fit it in, squeeze it in",
    "brief": "details of one named mission (ID like STK-01): time on target, package, crews, route, risk",
    "aircraft": "schedule and tasks of one named aircraft (ID like HLW-SU30-02)",
    "threat": "which planned routes one named threat (ID like SAM-MR-2) can reach, exposure to it",
    "readiness": "state of one base or all bases: aircraft, crews fit, weapons, munitions, stocks, closures",
    "fog": "the fog forecast (no change to the plan)",
    "robustness": "how the plan holds up on a bad day, what fails, what it depends on most",
    "coas": "alternative plans, courses of action, safer or more defensive options",
    "close_base": "one named base becomes unusable (fog, weather, runway bombed or cratered): re-plan around it",
    "aircraft_down": "one named aircraft is unserviceable, broken, grounded or lost: re-plan around it",
    "raise_priority": "make a named mission more important: change its priority",
    "cancel": "cancel, scrub or drop a named mission",
    "fog_closures": "close every base where fog is forecast",
    "spares": "hold idle aircraft as ground spares or standby reserves for the packages",
}
NEEDS = {"why_not": "mission", "what_would_it_take": "mission", "brief": "mission", "aircraft": "tail",
         "threat": "threat", "close_base": "base", "aircraft_down": "tail", "raise_priority": "mission",
         "cancel": "mission"}


class Ask(BaseModel):
    """A routed question: one tool and its arguments (times in scenario minutes)."""
    intent: str
    mission: str | None = None
    base: str | None = None
    tail: str | None = None
    threat: str | None = None
    start: int | None = None
    end: int | None = None
    priority: int | None = None
    threshold: float | None = None
    reason: str | None = None
    confident: bool = True      # False: the parser's catch-all guess; a configured model is asked first


class Action(BaseModel):
    label: str
    kind: str                       # "ask" | "select" | "open" | "propose"
    text: str | None = None         # ask: the follow-up question
    mission: str | None = None      # select
    base: str | None = None
    panel: str | None = None        # open: "coas" | "robustness" | "readiness" | "weather"
    events: list | None = None      # propose


class Reply(BaseModel):
    text: str
    intent: str | None = None
    router: str = "rules"
    tools: list[str] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    proposal: dict | None = None
    proposal_label: str | None = None


class Services(Protocol):
    """What the copilot may ask the server to do. Proposals are only proposals; a human approves."""
    def propose(self, events: list, label: str) -> dict: ...
    def propose_spares(self) -> dict: ...
    def robustness(self) -> dict: ...
    def coas(self) -> dict: ...
    def pending(self) -> bool: ...


# ---------------------------------------------------------------- entity extraction

def _norm(text: str) -> str:
    return re.sub(r"[\s_]+", "-", text.lower())


def _times(low: str) -> list[int]:
    """Clock times in order of appearance: 05:00, 0500 (after at/from/to/until/by/-), 5 am."""
    found = []
    for m in re.finditer(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", low):
        found.append((m.start(), int(m.group(1)) * 60 + int(m.group(2))))
    for m in re.finditer(r"(?:\b(?:at|from|to|until|till|by|after|before|between|and)\s+|-\s*)([01]\d|2[0-3])([0-5]\d)\b",
                         low):
        found.append((m.start(1), int(m.group(1)) * 60 + int(m.group(2))))
    for m in re.finditer(r"\b(1[0-2]|0?[1-9])\s*(am|pm)\b", low):
        h = int(m.group(1)) % 12 + (12 if m.group(2) == "pm" else 0)
        found.append((m.start(), h * 60))
    seen, out = set(), []
    for pos, t in sorted(found):
        if pos not in seen:
            seen.add(pos)
            out.append(t)
    return out


def _duration(low: str) -> int | None:
    m = re.search(r"\bfor\s+(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|min|mins|minutes)\b", low)
    if not m:
        return None
    v = float(m.group(1))
    return int(v * 60) if m.group(2).startswith("h") else int(v)


def entities(text: str, world: World) -> dict:
    """Missions, bases, aircraft, threats, times, priority and threshold mentioned in the question."""
    low, norm = text.lower(), _norm(text)
    prefixes = sorted({m.split("-")[0] for m in world.missions}, key=len, reverse=True)
    missions = []
    if prefixes:
        for m in re.finditer(rf"\b({'|'.join(p.lower() for p in prefixes)})\s*-?\s*0*(\d{{1,2}})\b", low):
            mid = f"{m.group(1).upper()}-{int(m.group(2)):02d}"
            if mid in world.missions and mid not in missions:
                missions.append(mid)
    bases = []
    for b in world.bases.values():
        if re.search(rf"\b{re.escape(b.name.lower())}\b", low) or re.search(rf"\b{b.id.lower()}\b", low):
            bases.append(b.id)
    tails = [t for t in world.aircraft if t.lower() in norm]
    if not tails:  # "su30-02 at halwara", "SU30 02"
        types = sorted({a.type for a in world.aircraft.values()}, key=len, reverse=True)
        for m in re.finditer(rf"\b({'|'.join(re.escape(t.lower()) for t in types)})-?0*(\d{{1,2}})\b", norm):
            cands = [t for t, a in world.aircraft.items() if a.type.lower() == m.group(1)
                     and t.endswith(f"-{int(m.group(2)):02d}") and (not bases or a.base in bases)]
            if len(cands) == 1:
                tails.append(cands[0])
    threats = [t for t in world.threats if t.lower() in norm]
    pr = re.search(r"\bp(?:riority)?\s*-?\s*(10|[1-9])\b", low) or re.search(r"\bto\s+(10|[1-9])\b", low)
    thr = re.search(r"\b(\d{1,2})\s*%", low)
    return {"missions": missions, "bases": bases, "tails": tails, "threats": threats, "times": _times(low),
            "duration": _duration(low), "priority": int(pr.group(1)) if pr else None,
            "threshold": int(thr.group(1)) / 100 if thr else None}


# ---------------------------------------------------------------- deterministic router

def _has(low: str, *pats: str) -> bool:
    return any(re.search(p, low) for p in pats)


def rules(text: str, world: World, plan: Plan | None, ctx: dict) -> Ask | None:
    low = text.lower().strip()
    e = entities(text, world)
    mission = e["missions"][0] if e["missions"] else None
    base = e["bases"][0] if e["bases"] else None
    tail = e["tails"][0] if e["tails"] else None
    threat = e["threats"][0] if e["threats"] else None
    whatif_q = _has(low, r"\bwhat if\b", r"\bsuppose\b", r"\bif\b", r"\bpropose\b", r"\bsimulate\b")
    if mission is None and ctx.get("mission") and _has(low, r"\b(it|its|this|that|this one|that one|the mission)\b"):
        mission = ctx["mission"]  # follow-up: "can we squeeze it in?"

    def ask(intent: str, **kw) -> Ask:
        return Ask(intent=intent, mission=kw.get("mission", mission), base=kw.get("base", base),
                   tail=kw.get("tail", tail), threat=kw.get("threat", threat), **{
                       k: v for k, v in kw.items() if k not in ("mission", "base", "tail", "threat")})

    if not low or _has(low, r"^\s*(help|\?)\s*$", r"what can you do", r"how do i use"):
        return Ask(intent="help")
    if _has(low, r"what would it take", r"how (can|could|do|would) (we|i) (get|plan|fly|fit)",
            r"\bget\b.*\bplanned\b", r"\bmake\b.*\b(work|planned|fit)\b", r"\bsqueeze\b", r"\bfit\b.*\bin\b"):
        return ask("what_would_it_take", mission=mission or ctx.get("mission"))
    if _has(low, r"\bspares?\b") and _has(low, r"\bhold\b", r"\badd\b", r"\bpropose\b", r"\buse\b", r"\bassign\b"):
        return Ask(intent="spares")
    if _has(low, r"\bfog\b") and _has(low, r"\bclose\b", r"\bclosures?\b", r"\bpropose\b", r"\bapply\b"):
        return Ask(intent="fog_closures", threshold=e["threshold"])
    closing = _has(low, r"\bfog", r"\bclos", r"\bshut", r"\bweather", r"\brain", r"\bstorm", r"\brunway",
                   r"\bcrater", r"\bbomb", r"\bhit\b", r"\bminima", r"\bsocked\b", r"\bunusable\b")
    if base and closing and (whatif_q or e["times"] or _has(low, r"\bclos", r"\bshut", r"\bsocked\b", r"\bbomb",
                                                             r"\bcrater", r"\bhit\b", r"\bunusable\b")):
        times, dur = e["times"], e["duration"]
        start = times[0] if times else None
        end = times[1] if len(times) > 1 else (start + dur if start is not None and dur else None)
        reason = ("runway cratered" if _has(low, r"runway", r"crater", r"bomb", r"\bhit\b")
                  else "fog, below minima" if "fog" in low else "weather below minima")
        return ask("close_base", start=start, end=end, reason=reason)
    if tail and (whatif_q or _has(low, r"\bu/?s\b", r"unserviceable", r"\bbreaks?\b", r"\blose\b", r"\blost\b",
                                  r"\bgrounded?\b", r"\bdown\b", r"\bfails?\b")):
        return ask("aircraft_down")
    if mission and _has(low, r"\braise\b", r"\bincrease\b", r"\bbump\b", r"\bset\b", r"\bmake\b", r"\bupgrade\b") \
            and _has(low, r"\bpriority\b", r"\bp\d"):
        return ask("raise_priority", priority=e["priority"] or 10)
    if mission and _has(low, r"\bcancel", r"\bscrub", r"\bdrop\b", r"\babort"):
        return ask("cancel")
    if _has(low, r"\bwhy\b", r"\bnot planned\b", r"\bunplanned\b", r"\bisn'?t planned\b", r"\bnot flying\b",
            r"\bnot tasked\b"):
        if mission or ctx.get("mission"):
            return ask("why_not", mission=mission or ctx.get("mission"))
        return Ask(intent="unplanned")
    if _has(low, r"course(s)? of action", r"\bcoas?\b", r"\boptions\b", r"\balternatives?\b", r"\bmin(imum)? risk\b",
            r"\bdefensive\b", r"\bintent\b"):
        return Ask(intent="coas")
    if _has(low, r"\brobust", r"\bbad day\b", r"\bfragile\b", r"single points?", r"\bdepend(s|ing)? (on|most)",
            r"\blean", r"\bspares?\b", r"\bwhat fails\b", r"\bp05\b", r"\brisk(y|iest)? mission"):
        return Ask(intent="robustness")
    if _has(low, r"\bfog\b", r"\bforecast\b", r"\bvisibility\b", r"\bweather\b"):
        return Ask(intent="fog", threshold=e["threshold"], base=base)
    if mission and _has(low, r"\bbrief", r"\btell me about\b", r"\bdetails?\b", r"\bwhat is\b", r"\bshow\b",
                        r"\bdescribe\b", r"\bsummar", r"\bwho\b", r"\bwhen\b", r"\bpackage\b", r"\broute\b"):
        return ask("brief")
    if threat:
        return ask("threat")
    if tail:
        return ask("aircraft")
    if _has(low, r"\breadiness\b", r"\bready\b", r"\bhow many\b", r"\bcrews?\b", r"\bstocks?\b", r"\bammo",
            r"\bweapons?\b", r"\bmunitions?\b", r"\bserviceab", r"\bfeeds?\b", r"\bfresh"):
        return Ask(intent="readiness", base=base)
    if _has(low, r"\bstatus\b", r"\bsummary\b", r"\boverview\b", r"\bhow (are we|is the plan|does it look)",
            r"\bkpis?\b", r"\bsitrep\b", r"\bsituation\b"):
        return Ask(intent="status")
    if mission:
        return ask("brief", confident=False)
    if base:
        return Ask(intent="readiness", base=base, confident=False)
    return None


# ---------------------------------------------------------------- optional local LLM router

def ask_schema(world: World) -> dict:
    """JSON schema for the LLM's single tool call; enums are the real IDs, so it can only choose."""
    def one_of(values: list[str]) -> dict:
        return {"enum": sorted(values) + [None]}
    clock = {"anyOf": [{"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$"}, {"type": "null"}]}
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": list(TOOLS)},
            "mission": one_of(list(world.missions)),
            "base": one_of(list(world.bases)),
            "tail": one_of(list(world.aircraft)),
            "threat": one_of(list(world.threats)),
            "start": clock,
            "end": clock,
            "priority": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 10}, {"type": "null"}]},
        },
        "required": ["intent", "mission", "base", "tail", "threat", "start", "end", "priority"],
        "additionalProperties": False,
    }


def system_prompt() -> str:
    """Static, so a local model server can keep it cached between questions."""
    tools = "\n".join(f"- {k}: {v}" for k, v in TOOLS.items())
    return (
        "You route a question from an air operations planner to exactly ONE tool of a planning system. "
        "Reply with JSON only. Fill only the arguments the question mentions, using IDs from the context; "
        "use null for anything not mentioned. Times are 24-hour HH:MM. If the question is not about the plan, "
        "choose help.\n"
        f"Tools:\n{tools}\n"
        "Mission IDs look like STK-01, base IDs like HLW, aircraft IDs like HLW-SU30-02 (base-type-number), "
        "threat IDs like SAM-MR-2."
    )


def context_note(world: World, plan: Plan | None, ctx: dict | None = None) -> str:
    planned = set(plan.assignments) if plan else set()
    missions = "; ".join(f"{m.id} {m.role.value} P{m.priority} {'planned' if m.id in planned else 'NOT planned'}"
                         for m in sorted(world.missions.values(), key=lambda m: m.id))
    bases = "; ".join(f"{b.id} {b.name}" for b in world.bases.values())
    earlier = ", ".join(f"{k} {v}" for k, v in (ctx or {}).items() if v)
    return (f"Context. Missions: {missions}\nBases: {bases}\nThreats: {', '.join(world.threats) or 'none'}\n"
            f"Time now {fmt_time(world.now)}." + (f" Earlier in this conversation: {earlier}." if earlier else ""))


EXAMPLES = [
    ("why on earth is STK-06 still sitting unplanned?", {"intent": "why_not", "mission": "STK-06"}),
    ("is there any way to fit STK-06 in?", {"intent": "what_would_it_take", "mission": "STK-06"}),
    ("Halwara is socked in from 0500 to 0930, what happens?",
     {"intent": "close_base", "base": "HLW", "start": "05:00", "end": "09:30"}),
    ("the runway at Sirsa just got hit", {"intent": "close_base", "base": "SRS"}),
    ("give me the rundown on the whole day", {"intent": "status"}),
    ("how is Pathankot looking?", {"intent": "readiness", "base": "PTK"}),
    ("what is AMB-RAFALE-03 flying today?", {"intent": "aircraft", "tail": "AMB-RAFALE-03"}),
]


class LLMRouter:
    """Maps a question to an Ask with a local model. `api`: "ollama" (native /api/chat with a JSON-schema
    format) or "openai" (any OpenAI-compatible /v1/chat/completions, e.g. llama.cpp's server)."""

    def __init__(self, url: str, model: str, api: str = "ollama", timeout: float = 60.0,
                 post: Callable[[str, dict, float], dict] | None = None):
        self.url, self.model, self.api, self.timeout = url.rstrip("/"), model, api, timeout
        self._post = post or _post_json

    @classmethod
    def from_env(cls) -> LLMRouter | None:
        url = os.environ.get("SARTHI_LLM_URL")
        if not url:
            return None
        return cls(url, os.environ.get("SARTHI_LLM_MODEL", "qwen2.5:3b"), os.environ.get("SARTHI_LLM_API", "ollama"),
                   float(os.environ.get("SARTHI_LLM_TIMEOUT", "45")))

    def ping(self) -> bool:
        path = "/api/tags" if self.api == "ollama" else "/models"
        with urllib.request.urlopen(f"{self.url}{path}", timeout=5) as r:
            return r.status == 200

    def describe(self) -> str:
        return f"{self.model} via {'Ollama' if self.api == 'ollama' else 'OpenAI-compatible server'} at {self.url}"

    def _messages(self, text: str, world: World, plan: Plan | None, ctx: dict | None = None) -> list[dict]:
        msgs = [{"role": "system", "content": system_prompt()}]
        keys = ["intent", "mission", "base", "tail", "threat", "start", "end", "priority"]
        for q, a in EXAMPLES:
            ids = [v for k, v in a.items() if k in ("mission", "base", "tail")]
            if all(v in world.missions or v in world.bases or v in world.aircraft for v in ids):
                msgs += [{"role": "user", "content": q},
                         {"role": "assistant", "content": json.dumps({k: a.get(k) for k in keys})}]
        msgs.append({"role": "user", "content": f"{context_note(world, plan, ctx)}\nQuestion: {text}"})
        return msgs

    def route(self, text: str, world: World, plan: Plan | None, ctx: dict | None = None) -> Ask | None:
        schema = ask_schema(world)
        msgs = self._messages(text, world, plan, ctx)
        try:
            if self.api == "ollama":
                body = {"model": self.model, "messages": msgs, "format": schema, "stream": False,
                        "options": {"temperature": 0}}
                content = self._post(f"{self.url}/api/chat", body, self.timeout)["message"]["content"]
            else:
                body = {"model": self.model, "messages": msgs, "temperature": 0, "max_tokens": 200,
                        "response_format": {"type": "json_schema",
                                            "json_schema": {"name": "route", "schema": schema, "strict": True}}}
                try:
                    out = self._post(f"{self.url}/chat/completions", body, self.timeout)
                except urllib.error.HTTPError:
                    # Servers that only take the schema inside a json_object response format (llama-cpp-python
                    # answers 500 to json_schema): retry once in that form.
                    body["response_format"] = {"type": "json_object", "schema": schema}
                    out = self._post(f"{self.url}/chat/completions", body, self.timeout)
                content = out["choices"][0]["message"]["content"]
            raw = json.loads(content)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return None
        a = validate_llm(raw, world)
        return ground(a, text, world, ctx) if a else None


def _post_json(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def validate_llm(raw: dict, world: World) -> Ask | None:
    """Keep only what exists: an unknown tool or ID is dropped, never trusted."""
    if not isinstance(raw, dict) or raw.get("intent") not in TOOLS:
        return None

    def pick(key: str, pool) -> str | None:
        v = raw.get(key)
        if not isinstance(v, str):
            return None
        hit = {k.lower(): k for k in pool}.get(v.strip().lower())
        return hit

    def clock(key: str) -> int | None:
        v = raw.get(key)
        if isinstance(v, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            return int(v[:2]) * 60 + int(v[3:])
        return None

    p = raw.get("priority")
    return Ask(intent=raw["intent"], mission=pick("mission", world.missions), base=pick("base", world.bases),
               tail=pick("tail", world.aircraft), threat=pick("threat", world.threats), start=clock("start"),
               end=clock("end"), priority=p if isinstance(p, int) and 1 <= p <= 10 else None)


# ---------------------------------------------------------------- answers (templates over engine output)

def ground(a: Ask, text: str, world: World, ctx: dict | None = None) -> Ask:
    """Trust the model for the choice of tool only. IDs and times come from the parser when it found them;
    an ID the question never mentions is dropped; a tool that needs one kind of ID falls back to the tool
    for the kind of ID the question does name."""
    e = entities(text, world)
    found = {"mission": e["missions"], "base": e["bases"], "tail": e["tails"], "threat": e["threats"]}
    upd = {}
    for k, ids in found.items():
        upd[k] = ids[0] if ids else None
    times = e["times"]
    if times:
        upd["start"] = times[0]
        upd["end"] = times[1] if len(times) > 1 else (times[0] + e["duration"] if e["duration"] else None)
    elif a.start is not None and not re.search(r"\d", text):
        upd["start"] = upd["end"] = None  # the question has no time in it
    if e["priority"]:
        upd["priority"] = e["priority"]
    a = a.model_copy(update=upd)
    need = NEEDS.get(a.intent)
    if need and getattr(a, need) is None and (ctx or {}).get(need) and re.search(
            r"\b(it|its|this|that|the mission|the aircraft|the base)\b", text.lower()):
        a = a.model_copy(update={need: ctx[need]})  # follow-up about the last one discussed
    if need and getattr(a, need) is None:
        if a.tail and need == "mission":
            a = a.model_copy(update={"intent": "aircraft"})
        elif a.mission and need == "tail":
            a = a.model_copy(update={"intent": "brief"})
        elif a.base and need in ("mission", "tail"):
            a = a.model_copy(update={"intent": "readiness"})
        elif a.threat:
            a = a.model_copy(update={"intent": "threat"})
    if a.intent == "fog_closures" and a.base and re.search(r"runway|bomb|crater|hit|struck|attack", text.lower()):
        a = a.model_copy(update={"intent": "close_base", "reason": "runway damaged"})
    return a


def pct(x: float, d: int = 1) -> str:
    return f"{x * 100:.{d}f}%"


def _mname(world: World, mid: str) -> str:
    m = world.missions[mid]
    return f"**{mid}** (P{m.priority}, {m.label or m.role.value})"


def _proposal_text(world: World, plan: Plan, prop: dict, what: str, focus: str | None = None) -> str:
    diff, naive = prop["diff"], prop.get("naive_diff")
    d = diff if isinstance(diff, dict) else diff.model_dump()
    nd = naive if (naive is None or isinstance(naive, dict)) else naive.model_dump()
    before = kpis(world, plan)["priority_weighted_fulfilment"]
    after = prop["kpis"]["priority_weighted_fulfilment"]
    dropped = [c["mission"] for c in d["changes"] if c["change"] == "DROPPED"]
    added = [c["mission"] for c in d["changes"] if c["change"] == "ADDED"]
    lines = [f"Proposal ready: {what}.",
             f"- **{d['aircraft_changes']} aircraft reassigned**"
             + (f" (a naive re-plan would change {nd['aircraft_changes']})" if nd else "")
             + f"; {d['untouched_missions']} missions untouched",
             f"- Mission fulfilment {pct(before)} → {pct(after)}"]
    if d.get("spare_changes") and not d["aircraft_changes"]:
        lines.append(f"- {d['spare_changes']} ground spare changes; no flying changes")
    if added:
        lines.append(f"- Added: {', '.join(added)}")
    if dropped:
        lines.append(f"- Dropped: {', '.join(dropped)}")
    new_plan = prop.get("plan")
    if focus and new_plan is not None and focus in world.missions:
        pa = new_plan.assignments if hasattr(new_plan, "assignments") else new_plan["assignments"]
        if focus in pa:
            tot = pa[focus].tot if hasattr(pa[focus], "tot") else pa[focus]["tot"]
            lines.append(f"- {focus} is planned (TOT {fmt_time(tot)})")
        else:
            un = new_plan.unassigned if hasattr(new_plan, "unassigned") else new_plan["unassigned"]
            lines.append(f"- {focus} is still not planned: {(un.get(focus) or ['no feasible option'])[0]}")
    lines.append("Nothing changes until you approve it on the right.")
    return "\n".join(lines)


def _routes_in_reach(world: World, plan: Plan, tid: str) -> list[tuple[str, float]]:
    t = world.threats[tid]
    reach, _ = effective_envelope(t, world.now)
    out = []
    for mid, a in plan.assignments.items():
        if any(haversine_km(t.lat, t.lon, la, lo) <= reach for s in a.sorties for la, lo in s.route):
            out.append((mid, max(s.risk for s in a.sorties)))
    return sorted(out, key=lambda x: -x[1])


def _trade(c: dict, ref: dict) -> str:
    """One plain sentence for a COA against the current-intent COA (same logic as the UI)."""
    k, rk, mt, rm = c["kpis"], ref["kpis"], c["metrics"], ref["metrics"]
    d_eff = (k["priority_weighted_fulfilment"] - rk["priority_weighted_fulfilment"]) * 100
    gains, costs = [], []
    if d_eff <= -0.5:
        costs.append(f"gives up {-d_eff:.1f} pts of effect")
    elif d_eff >= 0.5:
        gains.append(f"adds {d_eff:.1f} pts of effect")
    if rm["expected_losses"] > 0:
        cut = 1 - mt["expected_losses"] / rm["expected_losses"]
        if cut > 0.05:
            gains.append(f"cuts expected losses {cut:.0%} ({rm['expected_losses']:.1f} → {mt['expected_losses']:.1f})")
        elif cut < -0.05:
            costs.append(f"raises expected losses {-cut:.0%}")
    dm = rm["munitions_total"] - mt["munitions_total"]
    if dm > 0:
        gains.append(f"saves {dm} guided weapons")
    if costs and gains:
        return f"{c['name']} {' and '.join(costs)}; in return it {', '.join(gains)}."
    if costs:
        return f"{c['name']} {' and '.join(costs)}."
    return f"{c['name']} {', '.join(gains) or 'is equivalent on these measures'}."


def execute(ask: Ask, world: World, plan: Plan, svc: Services) -> Reply:
    i = ask.intent
    tools: list[str] = []
    acts: list[Action] = []
    planned = plan.assignments
    unplanned = sorted((m for m in world.missions.values() if m.id not in planned), key=lambda m: -m.priority)
    hadr = world.scenario == "hadr"

    def reply(text: str, **kw) -> Reply:
        return Reply(text=text, intent=i, tools=tools, actions=acts, **kw)

    proposing = i in ("close_base", "aircraft_down", "raise_priority", "cancel", "fog_closures", "spares")
    if proposing and svc.pending():
        return reply("A proposal is already waiting for your decision on the right. Approve or reject it first; "
                     "I won't stack a second change on top of it.")
    if i in NEEDS and getattr(ask, NEEDS[i]) is None:
        what = {"mission": "which mission (e.g. STK-06)", "tail": "which aircraft (e.g. HLW-SU30-02)",
                "threat": "which threat (e.g. SAM-MR-2)", "base": "which base (e.g. Halwara)"}[NEEDS[i]]
        return reply(f"For that I need to know {what}.")

    if i == "help":
        ex = [f"Why isn't {unplanned[0].id} planned?" if unplanned else "Why isn't STK-06 planned?",
              f"What would it take to plan {unplanned[0].id}?" if unplanned else "Brief me on STK-01",
              f"What if {next(iter(world.bases.values())).name} fogs in from 05:00 to 09:30?",
              "How robust is the plan?", "Readiness at " + next(iter(world.bases.values())).name,
              "Compare courses of action" if not hadr else "What is not planned?"]
        acts += [Action(label=q, kind="ask", text=q) for q in ex]
        return reply("I answer from the engine only: every number below comes from the planner, and anything that "
                     "would change the plan comes back as a proposal for you to approve or reject. Try:")

    if i == "status":
        tools.append("kpis")
        k = kpis(world, plan)
        lines = [f"**{k['missions_planned']} of {k['missions_total']} missions planned** · fulfilment "
                 f"{pct(k['priority_weighted_fulfilment'])} · expected value on the day {pct(k['expected_value'])}."]
        if hadr:
            lines.append(f"- Relief lifted: {k['cargo_planned_t']:.0f} of {k['cargo_total_t']:.0f} t requested")
        else:
            lines.append(f"- Intent: {world.intent.name} · mean sortie risk {pct(k['mean_sortie_risk'])} · "
                         f"{k['sorties']} sorties, {k['tanker_sorties']} tanker")
        if k["spares"]:
            lines.append(f"- {k['spares']} ground spares held")
        if unplanned:
            lines.append("- Not planned: " + ", ".join(f"{m.id} (P{m.priority})" for m in unplanned[:6])
                         + (" …" if len(unplanned) > 6 else ""))
            acts.append(Action(label=f"Why isn't {unplanned[0].id} planned?", kind="ask",
                               text=f"Why isn't {unplanned[0].id} planned?"))
        acts.append(Action(label="How robust is the plan?", kind="ask", text="How robust is the plan?"))
        return reply("\n".join(lines))

    if i == "unplanned":
        tools.append("explain")
        if not unplanned:
            return reply("Every mission is planned.")
        lines = [f"{len(unplanned)} missions are not planned:"]
        for m in unplanned[:8]:
            why = (plan.unassigned.get(m.id) or ["no explanation"])[0]
            lines.append(f"- {_mname(world, m.id)}: {why}")
        acts += [Action(label=f"What would it take for {m.id}?", kind="ask",
                        text=f"What would it take to plan {m.id}?") for m in unplanned[:2]]
        return reply("\n".join(lines))

    if i in ("why_not", "what_would_it_take", "brief") and ask.mission in planned and i != "brief":
        i = "brief"  # it is planned: brief instead

    if i == "why_not":
        tools.append(f"explain({ask.mission})")
        lines = [f"{_mname(world, ask.mission)} is not planned:"]
        lines += [f"- {w}" for w in plan.unassigned.get(ask.mission, ["No explanation available."])]
        acts += [Action(label="What would it take?", kind="ask", text=f"What would it take to plan {ask.mission}?"),
                 Action(label=f"Show {ask.mission}", kind="select", mission=ask.mission)]
        return reply("\n".join(lines))

    if i == "what_would_it_take":
        tools.append(f"what_would_it_take({ask.mission})")
        outs = whatif.what_would_it_take(world, plan, ask.mission, 3.0)
        if not outs:
            return reply(f"No single relaxation applies to {ask.mission}; the reasons are:\n"
                         + "\n".join(f"- {w}" for w in plan.unassigned.get(ask.mission, [])))
        lines = [f"What would get {_mname(world, ask.mission)} planned (each re-solved as a retask):"]
        for o in outs:
            if o.planned:
                lines.append(f"- ✓ {o.label} ({o.detail}): planned, {o.aircraft_changes} aircraft changed, "
                             f"{'drops ' + ', '.join(o.dropped) if o.dropped else 'nothing dropped'}, "
                             f"fulfilment {pct(o.fulfilment_before)} → {pct(o.fulfilment_after)}")
            else:
                lines.append(f"- ✕ {o.label}: still not planned")
        for o in [o for o in outs if o.planned][:2]:
            acts.append(Action(label=f"Propose: {o.label}", kind="propose", events=o.events,
                               text=f"{ask.mission}: {o.label}"))
        return reply("\n".join(lines))

    if i == "brief":
        mid = ask.mission
        m = world.missions[mid]
        tools.append(f"brief({mid})")
        a = planned.get(mid)
        head = (f"{_mname(world, mid)}: {m.role.value.lower()} at {m.lat:.2f}N {m.lon:.2f}E, "
                f"TOT window {fmt_time(m.tot_earliest)}-{fmt_time(m.tot_latest)}.")
        if a is None:
            acts.append(Action(label="Why not planned?", kind="ask", text=f"Why isn't {mid} planned?"))
            return reply(head + "\n- Not planned. " + (plan.unassigned.get(mid) or [""])[0])
        p = robust.success_p(world, plan).get(mid, 0.0)
        lines = [head, f"- Planned TOT **{fmt_time(a.tot)}**; P(success on the day) {pct(p, 0)}"]
        for s in a.sorties:
            lines.append(f"- {s.tail} (crew {(s.crew or '-').split('-')[-1]}) from {world.bases[s.base].name}: "
                         f"launch {fmt_time(s.launch)}, recover {fmt_time(s.recover)}, {s.route_km:.0f} km, "
                         f"risk {pct(s.risk)}{' · needs AAR' if s.needs_aar else ''}")
        if a.tankers:
            lines.append(f"- Tanker: {', '.join(a.tankers)}")
        if a.spares:
            lines.append(f"- Ground spare: {', '.join(s.tail for s in a.spares)}")
        if m.depends_on:
            d = planned.get(m.depends_on)
            lines.append(f"- Depends on {m.depends_on}" + (f" (TOT {fmt_time(d.tot)})" if d else " (NOT planned)"))
        deps = [x.id for x in world.missions.values() if x.depends_on == mid]
        if deps:
            lines.append(f"- Enables {', '.join(deps)}")
        acts.append(Action(label=f"Show {mid}", kind="select", mission=mid))
        return reply("\n".join(lines))

    if i == "aircraft":
        t = ask.tail
        ac = world.aircraft[t]
        tools.append(f"aircraft({t})")
        rows = []
        for mid, a in planned.items():
            rows += [(s.launch, f"{fmt_time(s.launch)}-{fmt_time(s.recover)} {mid}") for s in a.sorties if s.tail == t]
            rows += [(s.launch, f"{fmt_time(s.launch)}-{fmt_time(s.recover)} tanker for {mid}")
                     for s in a.tanker_sorties if s.tail == t]
            rows += [(s.launch, f"{fmt_time(s.launch)}-{fmt_time(s.recover)} ground spare for {mid}")
                     for s in a.spares if s.tail == t]
        lines = [f"**{t}** ({ac.type}, {world.bases[ac.base].name}): "
                 f"{'serviceable' if ac.serviceable else 'UNSERVICEABLE'}, P(serviceable) {pct(ac.p_serviceable, 0)}."]
        lines += [f"- {r}" for _, r in sorted(rows)] or ["- Not tasked: spare capacity."]
        if ac.serviceable and rows:
            acts.append(Action(label=f"What if {t} goes U/S?", kind="ask", text=f"What if {t} goes unserviceable?"))
        return reply("\n".join(lines))

    if i == "threat":
        tid = ask.threat
        th = world.threats[tid]
        tools.append(f"threat({tid})")
        reach, _ = effective_envelope(th, world.now)
        hits = _routes_in_reach(world, plan, tid)
        lines = [f"**{tid}** ({th.kind}, Pk {th.pk:.2f}): last fixed {fmt_time(th.observed_at)}; "
                 + (f"may have moved, so treated as reaching {reach:.0f} km." if th.mobile_kmh else
                    f"static, {th.radius_km:.0f} km envelope.")]
        lines += [f"- {mid} route passes within reach (worst sortie risk {pct(r)})" for mid, r in hits[:8]] \
            or ["- No planned route enters its envelope: routing goes around it."]
        return reply("\n".join(lines))

    if i == "readiness":
        tools.append("readiness")
        r = readiness(world, plan, world.now)
        bases = [b for b in r["bases"] if ask.base in (None, b["base"])]
        lines = []
        for b in bases:
            ac = ", ".join(f"{t['serviceable']}/{t['total']} {t['type']}" for t in b["types"]) or "no aircraft"
            low = [f"{w['weapon']} {w['left']}/{w['stock']}" for w in b["weapons"] if w["left"] < max(4, 0.25 * w["stock"])]
            clo = "; ".join(f"closed {fmt_time(c['start'])}-{fmt_time(c['end'])} ({c['reason']})" for c in b["closures"])
            if ask.base:
                fit = b["crews_fit_hourly"]
                lines += [f"**{b['name']}** at {fmt_time(r['at'])}:",
                          f"- Aircraft serviceable: {ac}; {sum(t['tasked'] for t in b['types'])} tasked ahead; "
                          f"{b['spares']} ground spares",
                          f"- Crews fit now {b['crews_fit_now']}/{b['crews_available']} "
                          f"(lowest {min(fit)} at {fmt_time(r['hours'][fit.index(min(fit))])})",
                          f"- Weapons left after the plan: "
                          + (", ".join(f"{w['weapon']} {w['left']}/{w['stock']}" for w in b["weapons"]) or "none held"),
                          f"- Alert reserve {b['reserve']} of {b['fighters']} fighters" if b["fighters"] else "- No fighters",
                          f"- {clo or 'Open all day'}"]
            else:
                lines.append(f"- **{b['name']}**: {ac}; crews fit {b['crews_fit_now']}/{b['crews_available']}"
                             + (f"; low: {', '.join(low)}" if low else "") + (f"; {clo}" if clo else ""))
        stale = [f["label"] for f in r["feeds"] if f["age_min"] > 360]
        if stale:
            lines.append(f"Old data (over 6 h): {', '.join(stale)}.")
        acts.append(Action(label="Open the readiness board", kind="open", panel="readiness"))
        return reply("\n".join(lines) if ask.base else "Readiness now:\n" + "\n".join(lines))

    if i in ("fog", "fog_closures"):
        if hadr:
            return reply("The fog model is trained on north Indian winter fog; it does not apply to the monsoon "
                         "flood scenario. Heavy rain closures and thunderstorm cells are in the plan instead.")
        thr = ask.threshold or 0.5
        tools.append(f"fog_forecast(threshold={thr:.0%})")
        fc = met.snapshot_forecast(world)
        wins = [w for w in met.fog_windows(fc, thr, after=world.now) if ask.base in (None, w.base)]
        if i == "fog_closures":
            events = met.closure_events(world, wins, world.now)
            if not events:
                return reply(f"No new closures at {thr:.0%}: the forecast windows are already closed.")
            tools.append("propose(base closures)")
            prop = svc.propose(events, f"Fog closures at {thr:.0%}")
            return reply(_proposal_text(world, plan, prop, f"close {len(events)} fog windows at P ≥ {thr:.0%}"),
                         proposal=prop, proposal_label=f"Copilot: fog closures at {thr:.0%}")
        if not wins:
            return reply(f"No base reaches P(fog) {thr:.0%} in the forecast ({fc.label}).")
        lines = [f"Fog forecast ({fc.label}), P(visibility < 1 km) ≥ {thr:.0%}:"]
        for w in sorted(wins, key=lambda w: (w.start, w.base))[:10]:
            lines.append(f"- {world.bases[w.base].name}: {fmt_time(w.start)}-{fmt_time(w.end)}, peak {pct(w.peak, 0)}")
        acts.append(Action(label=f"Propose closures at {thr:.0%}", kind="ask", text=f"Propose fog closures at {thr:.0%}"))
        return reply("\n".join(lines))

    if i == "robustness":
        tools.append("robustness(2000 runs)")
        r = svc.robustness()
        cur, hard = r["current"], r.get("hardened")
        lines = [f"Over {cur['runs']:,} simulated days: expected {pct(cur['mean'])}, bad day (p05) {pct(cur['p05'])}, "
                 f"good day (p95) {pct(cur['p95'])}."]
        if cur["fragile"]:
            f = cur["fragile"][0]
            cause = max(f["causes"], key=f["causes"].get)
            label = {"serviceability": "aircraft U/S at start-up", "tanker": "tanker no-show",
                     "dependency": "its SEAD failed", "attrition": "lost before target"}[cause]
            lines.append(f"- Most fragile: {f['mission']} (P{f['priority']}) fails {pct(f['p_fail'], 0)} of days, "
                         f"mostly because {label}")
        if cur["single_points"]:
            sp = cur["single_points"][0]
            lines.append(f"- The plan leans hardest on {sp['asset']}: losing it fails {', '.join(sp['missions'][:4])} "
                         f"(−{sp['value'] * 100:.1f} pts)")
            acts.append(Action(label=f"What if {sp['asset']} goes U/S?", kind="ask",
                               text=f"What if {sp['asset']} goes unserviceable?"))
        if hard:
            lines.append(f"- Holding {hard['spares']} idle aircraft as ground spares would lift the bad day to "
                         f"{pct(hard['p05'])} with no flying changes")
            acts.append(Action(label="Hold ground spares", kind="ask", text="Hold ground spares"))
        acts.append(Action(label="Open the robustness panel", kind="open", panel="robustness"))
        return reply("\n".join(lines))

    if i == "spares":
        tools.append("propose(ground spares)")
        if world.spare_policy:
            return reply("Ground spares are already held, and re-chosen after every retask.")
        prop = svc.propose_spares()
        return reply(_proposal_text(world, plan, prop, "hold idle aircraft as ground spares"),
                     proposal=prop, proposal_label="Copilot: hold ground spares")

    if i == "coas":
        if hadr:
            return reply("Courses of action trade effect against losses to an adversary; the flood-relief scenario "
                         "has none. Ask how robust the plan is instead.")
        tools.append("courses_of_action(3, parallel)")
        r = svc.coas()
        coas = [c if isinstance(c, dict) else c.model_dump() for c in r["coas"]]
        ref = next((c for c in coas if c["name"] == world.intent.name), coas[0])
        lines = [f"Three courses of action for the situation at {fmt_time(world.now)} (solved in {r['seconds']} s):"]
        for c in coas:
            lines.append(f"- **{c['name']}**: fulfilment {pct(c['kpis']['priority_weighted_fulfilment'])}, "
                         f"expected losses {c['metrics']['expected_losses']:.1f}, "
                         f"{c['metrics']['munitions_total']} guided weapons")
        lines += [_trade(c, ref) for c in coas if c is not ref]
        acts.append(Action(label="Open the COA panel", kind="open", panel="coas"))
        return reply("\n".join(lines))

    # ---- proposals (each goes through the normal approve / reject flow)
    at = world.now
    if i == "close_base":
        b = world.bases[ask.base]
        start = ask.start if ask.start is not None else at + 60
        start = max(start, at)
        end = ask.end if ask.end is not None and ask.end > start else start + 180
        reason = ask.reason or "weather below minima"
        ev = [BaseClosure(at=at, base=b.id, start=start, end=end, reason=reason)]
        what = f"close {b.name} {fmt_time(start)}-{fmt_time(end)} ({reason})"
    elif i == "aircraft_down":
        ev = [AircraftDown(at=at, tails=[ask.tail], reason="unserviceable (copilot what-if)")]
        what = f"{ask.tail} unserviceable from {fmt_time(at)}"
    elif i == "raise_priority":
        pr = ask.priority or 10
        ev = [PriorityChange(at=at, mission=ask.mission, priority=pr)]
        what = f"raise {ask.mission} to P{pr}"
    elif i == "cancel":
        ev = [CancelMission(at=at, mission=ask.mission)]
        what = f"cancel {ask.mission}"
    else:
        return reply("I can't do that yet.")
    tools.append(f"propose({ev[0].kind})")
    prop = svc.propose(ev, what)
    text = _proposal_text(world, plan, prop, what, focus=ask.mission if i == "raise_priority" else None)
    if i == "raise_priority":
        text = text.replace("- Mission fulfilment", "- Mission fulfilment (weighted by the new priorities)")
    return reply(text, proposal=prop, proposal_label=f"Copilot: {what}")


def route(text: str, world: World, plan: Plan | None, ctx: dict, llm: LLMRouter | None) -> tuple[Ask | None, str]:
    """Deterministic parser first; the local model only for questions the parser cannot place confidently."""
    a = rules(text, world, plan, ctx)
    if a is not None and a.confident:
        return a, "rules"
    if llm is not None:
        m = llm.route(text, world, plan, ctx)
        if m is not None:
            return m, f"llm:{llm.model}"
    if a is not None:
        return a, "rules"
    return None, "none"


def answer(text: str, world: World, plan: Plan, svc: Services, ctx: dict,
           llm: LLMRouter | None = None) -> Reply:
    ask, router = route(text, world, plan, ctx, llm)
    if ask is None:
        r = execute(Ask(intent="help"), world, plan, svc)
        r.text = ("I couldn't place that question" + ("" if llm else " (no local language model is configured, so "
                  "I only understand questions in the forms below)") + ". " + r.text.split(". ", 1)[-1])
        r.router, r.intent = router, None
        return r
    for k in ("mission", "base", "tail"):
        if getattr(ask, k):
            ctx[k] = getattr(ask, k)
    r = execute(ask, world, plan, svc)
    r.router = router
    return r


__all__ = ["Ask", "Action", "Reply", "Services", "LLMRouter", "TOOLS", "entities", "rules", "route", "execute",
           "answer", "ask_schema", "validate_llm"]
