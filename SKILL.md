---
name: jules-autoresearch
description: >
  Orchestrate an iterative autoresearch loop using Jules (Google's cloud coding
  agent) as the compute engine. Use this skill whenever the user wants to
  automatically tune, iterate, or optimize input keywords/prompts/parameters
  against a target metric - where each evaluation is expensive, slow, or
  token-heavy. Jules handles the heavy computation (running code, data
  processing, web research, analysis) in a cloud VM; Pi orchestrates the loop
  and reads results.

  Trigger on: "autoresearch", "iterate until metric is reached", "tune
  keywords", "loop until we get X", "run experiments", "use Jules for",
  "offload to Jules", "delegate to Jules", or any task where the user wants
  repetitive evaluation-and-refinement cycles delegated to cloud compute.
---

# Jules Autoresearch

```
initial keywords / params
        │
        ▼
┌─────────────────┐       target met?
│  Jules runs     │──YES──────────────► done ✓
│  the task in    │
│  a cloud VM     │
└─────────────────┘
        │ NO
        ▼
  parse results + new params (Jules suggests them)
        │
        └──────────────► next iteration
```

Jules does the heavy lifting — running code, evaluating metrics, proposing next
parameters. Pi orchestrates the loop and reads terminal results.

---

## Prerequisites

1. **Jules API key** — `jules.google.com/settings` → set `JULES_API_KEY`
2. **GitHub repo connected to Jules** — `jules.google.com` → Settings → Sources
3. **Python 3.8+** — stdlib only, no pip installs

```bash
python scripts/jules_client.py list-sources   # find your source name
```

---

## Quick Start

```bash
# Research mode (Jules reasons about the task)
python scripts/autoresearch.py \
  --source "sources/github-owner-repo" \
  --task "Find papers about: {keywords}" \
  --keywords "transformers, attention" \
  --target "5 papers with citations > 50 from 2022+" \
  --metric-type qualitative

# Eval-harness mode (Jules runs your eval script, measures exact metric)
python scripts/autoresearch.py \
  --source "sources/github-owner-repo" \
  --eval-script "scripts/eval_harness.py" \
  --params '{"queries": ["conference"], "exclusions": ["conference call"], "patterns": ["will present at"]}' \
  --target "precision >= 0.90 with recall >= 0.70" \
  --metric-type numeric --target-value 0.90 \
  --auto-pr \
  --parallel 3
```

---

## Two Modes

**Research mode** (default) — Jules reasons about an open-ended task and
returns `suggested_keywords` for the next iteration.

**Eval-harness mode** (`--eval-script`) — Jules runs a deterministic eval
script against a labelled dataset. Use whenever the metric can be computed
exactly. Jules returns `suggested_params` (structured dict). See
`references/eval-harness.md` for setup.

### Result JSON

Both modes end with Jules writing a JSON block in its final message:

```json
{
  "metric_value": 0.87,
  "target_met": false,
  "confidence": "medium",
  "suggested_keywords": "refined kw1, kw2",   ← research mode
  "suggested_params": {"queries": [...], ...}, ← eval-harness mode
  "rationale": "...",
  "summary": "...",
  "all_results": [...]                         ← eval-harness, parallel only
}
```

The script also scans bash outputs for this JSON as a fallback (eval scripts
that print results directly to stdout are handled automatically).

### Structured Parameters (`--params`)

Pass a JSON object instead of a flat keyword string when inputs are structured:

```bash
--params '{"efts_queries": ["conference"], "exclusions": ["conference call"], "patterns": ["will present at"]}'
# or from a file:
--params @params.json
```

`suggested_params` in the result mirrors this structure exactly. The loop
feeds it directly into the next iteration.

### Metric types

| Flag | Jules evaluates by |
|------|-------------------|
| `--metric-type qualitative` | Jules scores 0.0–1.0; stops at ≥ 0.8 |
| `--metric-type boolean` | Jules reports true/false |
| `--metric-type numeric --target-value 0.90` | Jules reports a float; stops at ≥ threshold |

---

## Configuration Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--task` | — | Task prompt. Use `{keywords}` placeholder. Optional in eval-harness mode |
| `--keywords` | — | Initial keyword string. Required if `--params` not set |
| `--params` | — | Initial params as JSON string or `@file.json` |
| `--target` | **required** | Natural language success description |
| `--eval-script` | — | Path to eval harness in repo. Enables eval-harness mode |
| `--source` | — | Jules source name (`sources/github-owner-repo`) |
| `--branch` | `main` | Starting branch |
| `--auto-pr` | false | Push branch + create PR when Jules finishes. **Always set this.** |
| `--metric-type` | `qualitative` | `numeric`, `boolean`, or `qualitative` |
| `--target-value` | — | Required for `numeric` |
| `--max-iterations` | `10` | Stop after N iterations |
| `--parallel` | `1` | Research: N sessions/iter. Eval-harness: N variants in 1 session |
| `--poll-interval` | `20` | Seconds between status checks |
| `--timeout` | `3600` | Session timeout in seconds |
| `--output-dir` | `autoresearch_results` | Where to save history/results |
| `--resume` | — | Resume from existing `history.json` |

---

## Operational Realities

**Session latency:** Jules takes **15-20 min to reach `COMPLETED`** even when
it finishes the actual work in under 2 minutes. This is Jules' internal
overhead, not your code. Default `--timeout 3600` is appropriate.

**Always use `--auto-pr`:** Without it, Jules holds the changeset internally
but never pushes a branch. The work is gone when the session expires.

**Branch targeting:** Jules is branch-scoped. If session 1 created branch
`jules/fix-abc`, pass `--branch jules/fix-abc` to session 2 or it won't see
session 1's changes.

**When Jules asks a question (`AWAITING_USER_FEEDBACK`):** The script prints
Jules' full question and the session URL, then keeps polling. Respond via the
**Jules web UI** — `sendMessage` via API key requires OAuth and is blocked.

**API key limits:** `sendMessage` and `list-activities` on some sessions
require OAuth, not API key. See `references/jules-client.md`.

---

## References

Read these on demand — they are not auto-loaded:

- `references/eval-harness.md` — setting up eval harness, labels.json, minimal eval script, task design tips
- `references/loop-operations.md` — running loop examples, reading history.json, mid-loop intervention, quota, troubleshooting
- `references/jules-client.md` — jules_client.py full CLI, Python module API, Jules CLI, API key limitations table
- `references/api_quickref.md` — full Jules REST API reference
- `references/eval_harness_template.py` — copy-paste eval harness starting point
