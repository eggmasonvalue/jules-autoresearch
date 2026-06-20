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

This skill lets you orchestrate an **autoresearch loop** using Jules as the compute engine:

```
initial keywords
     │
     ▼
┌──────────────┐       target met?
│  Jules runs  │──YES──────────────► done ✓
│  the task    │
└──────────────┘
     │ NO
     ▼
 parse results
 + new keywords (Jules suggests them)
     │
     └──────────────► next iteration
```

Jules does the heavy lifting - running code, doing research, evaluating your metric. Jules also proposes the next keyword set. Pi orchestrates the loop, reads results, and decides when to stop or intervene.

---

## Prerequisites

1. **Jules API key** - from `jules.google.com/settings`. Set as env var:
   ```bash
   export JULES_API_KEY="your-key-here"
   ```

2. **A GitHub repo connected to Jules** - connect it at `jules.google.com` → Settings → Sources. You need the `source` resource name (`sources/github-OWNER-REPO` or similar). Get it by running:
   ```bash
   python scripts/jules_client.py list-sources
   ```

3. **Python 3.8+** - only stdlib needed.

---

## Quick Start

```bash
# 1. Find your source name
python scripts/jules_client.py list-sources

# 2a. Research mode (open-ended, Jules reasons about the task)
python scripts/autoresearch.py \
  --source "sources/github-myuser-myrepo" \
  --task "Your research task: {keywords}" \
  --keywords "keyword1, keyword2" \
  --target "description of what success looks like" \
  --metric-type qualitative

# 2b. Eval-harness mode (structured params, Jules runs your eval script)
python scripts/autoresearch.py \
  --source "sources/github-myuser-myrepo" \
  --eval-script "scripts/eval_harness.py" \
  --params '{"efts_queries": ["conference"], "exclusions": ["conference call"], "patterns": ["will present at"]}' \
  --target "precision >= 0.90 with recall >= 0.70" \
  --metric-type numeric --target-value 0.90 \
  --parallel 3
```

---

## Two Modes

### Research mode (default)
Jules reasons about the task, does web/code research, and evaluates the metric
through its own judgment. Good for open-ended research, literature search,
qualitative evaluation.

```bash
python scripts/autoresearch.py \
  --task "Find SEC 8-K filings about: {keywords}" \
  --keywords "conference, investor day" \
  --target "find 10 filings with precision > 0.80" \
  --metric-type qualitative
```

### Eval-harness mode (`--eval-script`)
Jules runs a **deterministic eval script** in the repo against a labelled
dataset and reports numeric precision/recall/F1. Use this whenever your metric
can be computed exactly from code. Jules tunes the parameters, the script
measures objectively.

```bash
python scripts/autoresearch.py \
  --eval-script "scripts/eval_harness.py" \
  --params '{"efts_queries": ["conference"], "exclusions": ["conference call"], "patterns": ["will present at"]}' \
  --target "precision >= 0.90 with recall >= 0.70" \
  --metric-type numeric --target-value 0.90
```

Key difference: `--parallel N` in eval-harness mode means Jules evaluates **N
variants in a single session** (generating them via systematic ablation).
This saves quota - 1 Jules session instead of N.

### Result JSON

**Research mode** - Jules writes this at end of final message:
```json
{
  "metric_value": 0.72,
  "target_met": false,
  "confidence": "medium",
  "suggested_keywords": "better_kw1, refined_kw2",
  "rationale": "...",
  "summary": "..."
}
```

**Eval-harness mode** - Jules writes this after running all variants:
```json
{
  "metric_value": 0.87,
  "target_met": false,
  "suggested_params": {"efts_queries": [...], "exclusions": [...], "patterns": [...]},
  "rationale": "Adding 'presenting at' raised recall from 0.61 to 0.74 without precision loss...",
  "summary": "...",
  "all_results": [
    {"label": "baseline", "params": {...}, "metric_value": 0.82},
    {"label": "variant-1", "params": {...}, "metric_value": 0.87}
  ]
}
```

### Structured Parameters (`--params`)

Instead of a flat keyword string, pass a JSON object when your tunable inputs
are structured (multiple lists, nested config, etc.):

```bash
--params '{"efts_queries": ["conference", "fireside chat"], "exclusions": ["conference call"], "patterns": ["will present at", "presenting at", "participate in"]}'

# Or point to a file:
--params @params.json
```

`suggested_params` in the result JSON mirrors this structure - Jules returns
the same keys with modified values, which autoresearch.py automatically uses
as the next iteration's input.

### Metric Types

| Type | Flag | Jules evaluates by |
|------|------|-----------------------|
| Numeric | `--metric-type numeric --target-value 0.85` | Reports a float; compared with `>=` |
| Boolean | `--metric-type boolean` | Reports `true/false` |
| Qualitative | `--metric-type qualitative` | Jules scores 0.0-1.0; stops at ≥0.8 |

---

## Running the Loop

### Research mode example

```bash
python scripts/autoresearch.py \
  --source "sources/github-myuser-sandbox" \
  --task "Search for papers on: {keywords}" \
  --keywords "transformer, attention, self-attention" \
  --target "Find 5 papers published after 2022 with citation count > 50" \
  --metric-type qualitative \
  --max-iterations 8
```

### Eval-harness mode example

```bash
python scripts/autoresearch.py \
  --source "sources/github-myuser-classifier-repo" \
  --eval-script "scripts/eval_harness.py" \
  --params '{"efts_queries": ["conference", "\"fireside chat\""], "exclusions": ["conference call"], "patterns": ["will present at", "presenting at", "participate in"]}' \
  --target "precision >= 0.90 with recall >= 0.70" \
  --metric-type numeric --target-value 0.90 \
  --parallel 3 \
  --max-iterations 8
```

### Watch progress

Results are saved to `autoresearch_results/` as the loop runs:
- `autoresearch_results/iteration_N/session_ID.json` - raw Jules session outputs
- `autoresearch_results/history.json` - full iteration history with params at each step
- `autoresearch_results/best.json` - best result so far

---

## Reading Results

After the loop ends, read `autoresearch_results/history.json`:
```json
{
  "iterations": [
    {
      "iteration": 1,
      "keywords": "keyword1, keyword2",
      "sessions": ["sessions/123456"],
      "metric_value": 0.72,
      "target_met": false,
      "rationale": "...",
      "suggested_keywords": "better_kw1, refined_kw2"
    }
  ],
  "final_keywords": "...",
  "target_met": true,
  "total_sessions_used": 7
}
```

---

## Intervening Mid-Loop

The loop pauses and asks for input when:
- `--require-plan-approval` is set (Jules waits before executing)
- A session enters `AWAITING_USER_FEEDBACK` state
- You press Ctrl+C (graceful stop; saves state to `history.json`)

### When Jules asks a question (`AWAITING_USER_FEEDBACK`)

The polling script fetches Jules' full question and prints it with the session
URL — then keeps polling until Jules continues:

```
  ============================================================
  JULES HAS A QUESTION (session 1234567890)
  ============================================================
  I can see two approaches here — should I prioritise precision
  or recall in the initial parameter set?
  ------------------------------------------------------------
  Respond at: https://jules.google.com/session/1234567890
  Polling resumes automatically once you reply.
  ============================================================
```

**Important:** `sendMessage` via API key is blocked by Google (requires OAuth).
You must respond through the **Jules web UI** at the printed URL. The polling
loop resumes automatically as soon as Jules receives your reply — you don't
need to restart anything.

---

## Using the Jules Client Directly

For one-off Jules interactions (outside the autoresearch loop):

```bash
# List connected repos
python scripts/jules_client.py list-sources

# Create a single session
python scripts/jules_client.py create-session \
  --source "sources/github-myuser-repo" \
  --prompt "Fix the bug in auth.py" \
  --branch main \
  --title "Bug fix"

# Check session status
python scripts/jules_client.py get-session SESSION_ID

# Wait for completion and print outputs
python scripts/jules_client.py wait SESSION_ID

# List all sessions
python scripts/jules_client.py list-sessions

# Delete a session
python scripts/jules_client.py delete-session SESSION_ID
```

---

## Jules CLI Alternative

If the Jules CLI (`@google/jules`) is installed:
```bash
# Equivalent of creating a session
jules remote new --repo owner/repo --session "Your task prompt"

# List sessions
jules remote list --session

# Pull results
jules remote pull --session SESSION_ID
```

The Python client is preferred for scripted loops; the CLI is good for quick one-offs.

---

## Operational realities (learned from testing)

**Session latency:** Jules sessions typically take **15-20 minutes** to reach
`COMPLETED` even when Jules finishes the actual work in under 2 minutes. The
rest is Jules' internal state-transition overhead. Set `--timeout 3600`
(default) and don't worry about it.

**Always use `--auto-pr`** (or set `automationMode: AUTO_CREATE_PR` when
calling the API directly). Without it, Jules produces a changeset internally
but never pushes a branch or creates a PR — the work is lost when the session
expires. This applies to all sessions that involve code changes.

**Branch targeting:** Jules works off a starting branch. If a previous Jules
session created a branch, point the next session at that branch (not `main`)
or its changes won't be visible to Jules.

**API key limitations:** The REST API key supports `GET /sessions`,
`POST /sessions` (create), `DELETE /sessions`, and `GET /sessions/{id}`.
It does **not** work for `POST .../sendMessage` or `GET .../activities` on
certain sessions — those require OAuth. Use the CLI or web UI for interactive
steps.

---

## Eval Harness Pattern (for deterministic metrics)

When your metric can be computed by code, use this pattern instead of asking
Jules to estimate it. It's more accurate, reproducible, and converges faster.

### What you need in your repo

```
your-repo/
├── data/
│   └── labels.json          # Ground truth (~100 labelled examples)
├── scripts/
│   ├── classifier.py        # The thing you're tuning (accepts --params)
│   └── eval_harness.py      # Runs classifier on labels, reports metrics
└── ...
```

### labels.json format

```json
[
  {"id": "acc-001", "text": "<filing text>", "label": "CONFERENCE_ATTENDANCE"},
  {"id": "acc-002", "text": "<filing text>", "label": "OTHER"},
  ...
]
```

Build this once manually. ~100 examples is enough to get stable precision/recall
estimates. Include a realistic mix of true positives and negatives.

### eval_harness.py contract

See `references/eval_harness_template.py` for a copy-paste starting point.

The script **must**:
- Accept `--params '<json>'` as a CLI argument
- Print one JSON object to stdout: `{"metric_value": <float>, "target_met": <bool>, "details": {...}}`
- Exit 0 regardless of whether the target is met

The `details` field is important - it tells Jules which examples are failing
(FPs and FNs), which drives better keyword suggestions.

### Minimal eval_harness.py

```python
import argparse, json, sys
from pathlib import Path

def classify(text, params):
    import re
    for excl in params.get("exclusions", []):
        if excl.lower() in text.lower():
            return False
    for pat in params.get("patterns", []):
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--params", required=True)
    args = p.parse_args()
    params = json.loads(args.params)

    labels = json.loads(Path("data/labels.json").read_text())
    tp = fp = fn = tn = 0
    fps = []
    fns = []
    for item in labels:
        pred = classify(item["text"], params)
        pos = item["label"] == "CONFERENCE_ATTENDANCE"
        if pred and pos:     tp += 1
        elif pred and not pos: fp += 1; fps.append(item["id"])
        elif not pred and pos: fn += 1; fns.append(item["id"])
        else:                tn += 1

    prec = tp / (tp + fp) if tp + fp else 0
    rec  = tp / (tp + fn) if tp + fn else 0
    print(json.dumps({
        "metric_value": round(prec, 4),
        "target_met": prec >= 0.90 and rec >= 0.70,
        "details": {"precision": prec, "recall": rec,
                    "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                    "false_positive_ids": fps[:5],
                    "false_negative_ids": fns[:5]}
    }))

if __name__ == "__main__":
    main()
```

---

## Designing Good Autoresearch Tasks

Jules is a coding agent running in a VM with your GitHub repo.

**Research mode:** The task prompt should:
1. Use `{keywords}` as the placeholder for the current keyword set
2. Tell Jules exactly what metric to compute and how
3. Be specific - avoid ambiguous multi-step tasks

**Eval-harness mode:** The task is defined by the eval script. You don't need
a `--task` description; Jules already knows to run `--eval-script`. Focus
your energy on making `labels.json` comprehensive and `eval_harness.py` fast.

---

## Configuration Reference

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--task` | No* | — | Task prompt. Use `{keywords}` as placeholder. *Optional in eval-harness mode |
| `--keywords` | No† | — | Initial keyword string (research mode). †Required if `--params` not set |
| `--params` | No† | — | Initial parameters as JSON string or `@file.json` (eval-harness mode) |
| `--target` | **Yes** | — | Natural language success description |
| `--eval-script` | No | — | Path to eval harness in the repo. Enables eval-harness mode |
| `--source` | No | — | Jules source name (`sources/github-owner-repo`) |
| `--branch` | No | `main` | Starting branch |
| `--metric-type` | No | `qualitative` | `numeric`, `boolean`, or `qualitative` |
| `--target-value` | Cond. | — | Required for `numeric` type |
| `--max-iterations` | No | `10` | Max iterations before stopping |
| `--parallel` | No | `1` | Research: Jules sessions per iteration. Eval-harness: variants per session |
| `--poll-interval` | No | `20` | Seconds between status checks |
| `--timeout` | No | `3600` | Seconds before session timeout |
| `--output-dir` | No | `autoresearch_results` | Where to save results |
| `--require-plan-approval` | No | `false` | Pause for plan review |
| `--auto-pr` | No | `false` | Create PR for code changes |
| `--resume` | No | — | Resume from existing `history.json` |

---

## Quota Management

You have **15 parallel sessions/day** and **100 total/day** on Google AI Pro. The loop tracks usage and warns you when approaching limits. Rules of thumb:
- `--parallel 5` with `--max-iterations 10` = up to 50 sessions
- `--parallel 15` with `--max-iterations 6` = up to 90 sessions (close to limit)
- Keep `--parallel` ≤ 5 for iterative tasks; parallel is best for embarrassingly parallel variants

---

## Troubleshooting

**`JULES_API_KEY not set`** → Set the env var or create `.env` with `JULES_API_KEY=...`

**`Session FAILED`** → Check `session.json` for the failure reason. Jules may have encountered a dependency issue, permission error, or the task was unclear.

**`AWAITING_USER_FEEDBACK` (stuck)** → Jules has a question. The polling
script prints Jules' full question and the session URL. Open the URL in your
browser and reply there. Polling resumes automatically.

**Note:** `sendMessage` via REST API key is blocked by Google — it requires
OAuth. There is no programmatic workaround; the web UI is the only way.

**Source not found** → The GitHub repo must be connected at `jules.google.com` before the API can use it.

**Metric never converges** → Jules may need more specific evaluation instructions. Try adding detail to `--target` or adding explicit evaluation code to your repo that Jules can run.

---

## References

- `references/api_quickref.md` - Full Jules REST API quick reference
- Jules docs: https://jules.google/docs/api/reference/overview
- CLI docs: https://jules.google/docs/cli/reference
