"""
Optional LLM adapter for the AI Researcher.

SECURITY MODEL (non-negotiable):
  * The LLM receives a STRUCTURED SUMMARY of experiment results (JSON), not
    raw code, and must reply with JSON hypotheses.
  * Replies are validated against the SAME whitelist as the rule analyzer
    (ALLOWED_ACTIONS). Anything else is rejected.
  * The LLM can NEVER execute code, send orders, touch risk settings, or
    bypass validation. A hypothesis only becomes a strategy child through
    genome.ops.directed + the normal evolution/validation pipeline.
  * Disabled by default (ai.llm_provider = "none").
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from ..config import get_config
from .analyzer import ALLOWED_ACTIONS, Hypothesis

log = logging.getLogger("ai.llm")

SYSTEM_PROMPT = """You are a quantitative research assistant inside an evolutionary
trading research lab. You analyze structured backtest/validation results and propose
research hypotheses as JSON. You MUST NOT propose executable code, order placement,
or changes to risk limits. Reply with a JSON array (max 3 items) of objects:
{
  "strategy_id": <int>,
  "observation": "<what the data shows>",
  "hypothesis": "<what to test>",
  "action": "<one of the allowed actions>",
  "params": { ... }
}
Allowed actions and params:
  add_regime_filter {regime: trending|ranging|breakout|high_volatility|low_volatility|expansion|compression|momentum}
  restrict_sessions {sessions: [asia|london|newyork|london_ny_overlap, ...]}
  exclude_day {day: 0..4}   (0=Monday)
  restrict_days {days: [0..4,...]}
  change_timeframe {timeframe: M1|M5|M15|M30|H1}
  add_adx_filter {threshold: number}
  add_feature_condition {spec: "<feature spec>", cmp: ">|<|>=|<=", value: number}
  restrict_direction {direction: long|short}
  tighten_sl {factor: 0.5..1.0}
  widen_tp {factor: 1.0..2.0}
"""


def _summarize(rows: List[Dict]) -> str:
    out = []
    for r in rows[:10]:
        try:
            genome = json.loads(r["genome"]) if isinstance(r["genome"], str) else r["genome"]
        except Exception:
            continue
        out.append({
            "strategy_id": r["id"], "fitness": r.get("fitness"),
            "status": r.get("status"), "timeframe": r.get("timeframe"),
            "features": genome.get("features"),
            "direction": genome.get("direction"),
            "sessions": genome.get("sessions"),
            "regime_filters": genome.get("regime_filters"),
        })
    return json.dumps(out)


def llm_hypotheses(rows: List[Dict]) -> List[Dict]:
    cfg = get_config().ai
    if cfg.llm_provider.lower() in ("none", ""):
        return []
    api_key = os.environ.get(cfg.llm_api_key_env, "")
    if not cfg.llm_endpoint or not api_key:
        log.info("LLM researcher configured but endpoint/key missing — skipped")
        return []
    try:
        import httpx
        payload = {
            "model": cfg.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",
                 "content": "Experiment summary:\n" + _summarize(rows) +
                            "\nPropose up to 3 hypotheses as a JSON array."},
            ],
            "temperature": 0.4,
        }
        resp = httpx.post(cfg.llm_endpoint,
                          headers={"Authorization": f"Bearer {api_key}"},
                          json=payload, timeout=60.0)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        # strip code fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        items = json.loads(content)
    except Exception as e:
        log.warning("LLM researcher failed: %s", e)
        return []

    out: List[Dict] = []
    if not isinstance(items, list):
        return []
    for it in items[:3]:
        try:
            if it.get("action") not in ALLOWED_ACTIONS:
                log.warning("LLM proposal rejected (action not whitelisted): %s",
                            it.get("action"))
                continue
            out.append(Hypothesis.make(int(it.get("strategy_id", 0)) or None,
                                       "llm", str(it.get("observation", ""))[:500],
                                       str(it.get("hypothesis", ""))[:500],
                                       it["action"], it.get("params") or {}))
        except Exception as e:
            log.warning("LLM proposal rejected: %s", e)
    return out
