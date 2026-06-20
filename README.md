# jules-autoresearch

An iterative autoresearch loop skill for [pi](https://github.com/earendil-works/pi-coding-agent) that uses [Jules](https://jules.google/) (Google's cloud coding agent) as the compute engine.

## What it does

Instead of spending your own tokens on repetitive evaluation-and-refinement cycles, you delegate the heavy work to Jules. Pi orchestrates the loop; Jules runs the actual task, measures the metric, and suggests the next parameter set.

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

## Two modes

**Research mode** — Jules reasons about an open-ended task and suggests improved keywords each iteration.

**Eval-harness mode** — Jules runs a deterministic eval script (e.g. `python eval_harness.py --params '...'`) against a labelled dataset, reports precision/recall/F1, and proposes better parameters. Zero qualitative guesswork.

## Quick start

```bash
# 1. Set your Jules API key (from jules.google.com/settings)
export JULES_API_KEY="your-key-here"

# 2. Find your connected GitHub repo
python scripts/jules_client.py list-sources

# 3a. Research mode
python scripts/autoresearch.py \
  --source "sources/github-owner-repo" \
  --task "Find papers about: {keywords}" \
  --keywords "transformers, attention" \
  --target "5 papers with >50 citations from 2022+" \
  --metric-type qualitative

# 3b. Eval-harness mode (structured params + your eval script)
python scripts/autoresearch.py \
  --source "sources/github-owner-repo" \
  --eval-script "scripts/eval_harness.py" \
  --params '{"queries": ["conference"], "exclusions": ["conference call"], "patterns": ["will present at"]}' \
  --target "precision >= 0.90 with recall >= 0.70" \
  --metric-type numeric --target-value 0.90 \
  --parallel 3
```

## Files

```
├── SKILL.md                      # Full skill instructions for pi
├── scripts/
│   ├── jules_client.py           # Jules REST API wrapper (pure stdlib)
│   └── autoresearch.py           # Autoresearch loop orchestrator
└── references/
    ├── api_quickref.md           # Jules API quick reference
    └── eval_harness_template.py  # Copy-paste starting point for your eval script
```

## Requirements

- Python 3.8+, no external dependencies (pure stdlib)
- A Jules account with API key ([jules.google.com](https://jules.google.com))
- A GitHub repo connected to Jules

## Notes

- Jules sessions take 15–20 min to complete (most of that is Jules' internal state transitions, not actual work time)
- `sendMessage` requires OAuth — if Jules asks a question mid-session, the polling script surfaces the question and session URL so you can respond via the Jules web UI
- `--parallel N` in eval-harness mode means Jules evaluates N parameter variants in a single session (quota-efficient); in research mode it dispatches N separate sessions

## As a pi skill

Drop the `jules-autoresearch/` directory into `~/.agents/skills/` and pi will load it automatically.
