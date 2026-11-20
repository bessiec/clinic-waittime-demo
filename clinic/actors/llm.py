"""Model calls for the ACTOR agents only (front desk and patients).

Product agents (Milestone 5) get their own module and prompts; nothing here is
shared with them, so the demo never grades itself with the same model call.

Three callers, one interface: call(t, actor, role, prompt_file, prompt, schema)
returns the agent's answer as a dict, or None when the agent failed (bad JSON,
timeout, call budget used up). The caller of call() then falls back to the
scripted rule and the failure stays in the log.

  ClaudeCodeCaller  runs the Claude Code CLI headless on the owner's subscription.
                    API-key variables are removed from its environment so it can
                    never bill an API account. It runs in an empty folder with no
                    settings, tools, MCP servers or skills, so the persona sees
                    nothing but its own prompt.
  ReplayCaller      returns the replies recorded in an earlier run, in order, and
                    stops the run if a prompt differs from the recording (the
                    replay would no longer be the same day).
Every call, live or replayed, is written to the run's llm_calls table."""
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from ..config import ROOT

PROMPTS = ROOT / "prompts" / "actors"
_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
              "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


class ReplayMismatch(RuntimeError):
    pass


def load_prompt(name: str) -> str:
    """Persona prompt plus the shared rules every actor follows."""
    rules = (PROMPTS / "_rules.md").read_text()
    return (PROMPTS / f"{name}.md").read_text().strip() + "\n\n" + rules.strip()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def find_claude(cfg) -> str:
    cand = [cfg["actors"].get("claude_bin") or "", os.environ.get("CLAUDE_BIN", ""), shutil.which("claude") or ""]
    cand += sorted(glob.glob(os.path.expanduser(
        "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude")))[::-1]
    for c in cand:
        if c and os.access(c, os.X_OK):
            return c
    raise FileNotFoundError("Claude Code CLI not found. Set CLAUDE_BIN or [actors] claude_bin.")


class _Base:
    source = "base"

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.seq = 0
        self.max_calls = cfg["actors"]["max_calls_per_run"]

    def _log(self, t, actor, role, prompt_file, prompt, out, ok, error, meta):
        self.store.add_llm_call({
            "seq": self.seq, "t": t, "actor": actor, "role": role, "prompt_file": prompt_file,
            "prompt_sha": sha(prompt_file + "\n" + prompt), "prompt": prompt,
            "response": json.dumps(out) if out is not None else None, "ok": int(ok), "error": error,
            "source": self.source, **meta})

    def call(self, t, actor, role, prompt_file, prompt, schema):
        self.seq += 1
        out, error, meta = self._call(t, actor, role, prompt_file, prompt, schema)
        ok = out is not None and _valid(out, schema)
        if out is not None and not ok:
            error = error or "reply did not match the schema"
        self._log(t, actor, role, prompt_file, prompt, out, ok, error, meta)
        return out if ok else None


class ClaudeCodeCaller(_Base):
    source = "claude-code"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        self.bin = find_claude(cfg)
        self.cwd = ROOT / "runs" / ".actor_cwd"     # empty folder: no CLAUDE.md, no memory, no project files
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
        self.system = {}

    def _call(self, t, actor, role, prompt_file, prompt, schema):
        if self.seq > self.max_calls:
            return None, f"call budget of {self.max_calls} used up", {"source": "skipped"}
        system = self.system.setdefault(prompt_file, load_prompt(prompt_file))
        cmd = [self.bin, "-p", prompt, "--model", self.cfg["actors"]["model"],
               "--system-prompt", system, "--json-schema", json.dumps(schema),
               "--output-format", "json", "--no-session-persistence", "--tools", "",
               "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands"]
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=self.cwd, env=self.env, capture_output=True, text=True,
                               timeout=self.cfg["actors"]["call_timeout_seconds"])
        except subprocess.TimeoutExpired:
            return None, "timeout", {"ms": int((time.time() - t0) * 1000)}
        ms = int((time.time() - t0) * 1000)
        h, m = divmod(t, 60)
        print(f"  agent call {self.seq}: {actor}/{role} at {h:02d}:{m:02d} sim time ({ms / 1000:.0f}s)", file=sys.stderr, flush=True)
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            return None, f"no JSON from CLI (exit {r.returncode}): {(r.stderr or r.stdout)[:200]}", {"ms": ms}
        usage = d.get("usage") or {}
        models = [m for m in (d.get("modelUsage") or {}) if "haiku" not in m] or list(d.get("modelUsage") or {})
        meta = {"ms": ms, "model": models[0] if models else None,
                "input_tokens": (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
                + (usage.get("cache_read_input_tokens") or 0),
                "output_tokens": usage.get("output_tokens"),
                # What these calls would cost at API list prices. Billed: $0 (subscription).
                "modeled_usd": d.get("total_cost_usd")}
        if d.get("is_error"):
            return None, f"CLI error: {str(d.get('result'))[:200]}", meta
        return d.get("structured_output"), None, meta


class ReplayCaller(_Base):
    source = "replay"

    def __init__(self, cfg, store, replay_from):
        super().__init__(cfg, store)
        con = sqlite3.connect(replay_from)
        con.row_factory = sqlite3.Row
        self.rows = {r["seq"]: dict(r) for r in con.execute("SELECT * FROM llm_calls ORDER BY seq")}
        con.close()
        if not self.rows:
            raise ValueError(f"{replay_from} has no recorded agent calls")

    def _call(self, t, actor, role, prompt_file, prompt, schema):
        r = self.rows.get(self.seq)
        if r is None or r["prompt_sha"] != sha(prompt_file + "\n" + prompt):
            raise ReplayMismatch(f"call {self.seq} ({actor}/{role} at t={t}) differs from the recording; "
                                 "the replay is no longer the same day")
        meta = {k: r[k] for k in ("ms", "model", "input_tokens", "output_tokens", "modeled_usd")}
        if r["source"] == "skipped":
            meta["source"] = "skipped"
        out = json.loads(r["response"]) if r["response"] else None
        return out, r["error"], meta


def _valid(out, schema):
    """Small JSON-schema check for the shapes used here: object, required keys,
    types, enums, integer ranges and arrays."""
    def ok(v, s):
        ty = s.get("type")
        types = ty if isinstance(ty, list) else [ty]
        if v is None:
            return "null" in types
        if "enum" in s and v not in s["enum"]:
            return False
        if "object" in types and isinstance(v, dict):
            if any(k not in v for k in s.get("required", [])):
                return False
            return all(ok(v[k], s["properties"][k]) for k in v if k in s.get("properties", {}))
        if "array" in types and isinstance(v, list):
            return all(ok(x, s.get("items", {})) for x in v)
        if "integer" in types and isinstance(v, int) and not isinstance(v, bool):
            return s.get("minimum", v) <= v <= s.get("maximum", v)
        if "string" in types and isinstance(v, str):
            return len(v) <= s.get("maxLength", 10 ** 6)
        if "boolean" in types and isinstance(v, bool):
            return True
        return False
    return ok(out, schema)


def make_caller(mode, cfg, store, replay_from=None):
    if mode == "llm":
        return ClaudeCodeCaller(cfg, store)
    if mode == "replay":
        return ReplayCaller(cfg, store, replay_from)
    return None
