# Loop Operations

## Running the loop

### Research mode (open-ended)

```bash
python scripts/autoresearch.py \
  --source "sources/github-myuser-sandbox" \
  --task "Search for papers on: {keywords}" \
  --keywords "transformer, attention, self-attention" \
  --target "Find 5 papers published after 2022 with citation count > 50" \
  --metric-type qualitative \
  --max-iterations 8
```

### Eval-harness mode (deterministic metric)

```bash
python scripts/autoresearch.py \
  --source "sources/github-myuser-repo" \
  --eval-script "scripts/eval_harness.py" \
  --params '{"efts_queries": ["conference", "\"fireside chat\""], "exclusions": ["conference call"], "patterns": ["will present at", "presenting at"]}' \
  --target "precision >= 0.90 with recall >= 0.70" \
  --metric-type numeric --target-value 0.90 \
  --auto-pr \
  --parallel 3 \
  --max-iterations 8
```

### Resuming a stopped run

```bash
python scripts/autoresearch.py \
  --source "..." --eval-script "..." --params "..." --target "..." \
  --metric-type numeric --target-value 0.90 \
  --output-dir autoresearch_results/my-run \
  --resume
```

---

## Reading results

Results are saved to `autoresearch_results/` (or `--output-dir`) as the loop runs:

```
autoresearch_results/
├── history.json          # Full iteration history
├── best.json             # Best result so far
└── iteration_N/
    └── session_ID.json   # Raw Jules outputs per session
```

`history.json` structure:

```json
{
  "iterations": [
    {
      "iteration": 1,
      "params": {"efts_queries": [...], "exclusions": [...], "patterns": [...]},
      "keywords": "...",
      "sessions": ["sessions/123456"],
      "metric_value": 0.72,
      "target_met": false,
      "rationale": "...",
      "suggested_params": {...},
      "all_results": [...]
    }
  ],
  "final_params": {...},
  "target_met": true,
  "total_sessions_used": 7
}
```

---

## Intervening mid-loop

### When Jules asks a question (`AWAITING_USER_FEEDBACK`)

The polling script fetches Jules' full question and prints it with the session URL:

```
  ============================================================
  JULES HAS A QUESTION (session 1234567890)
  ============================================================
  I can see two approaches — should I prioritise precision
  or recall in the initial parameter set?
  ------------------------------------------------------------
  Respond at: https://jules.google.com/session/1234567890
  Polling resumes automatically once you reply.
  ============================================================
```

Go to the URL, read the question, reply in the Jules web UI. The polling loop
resumes automatically — you don't need to restart anything.

**`sendMessage` via API key is blocked by Google (requires OAuth).** The web UI
is the only way to reply.

### Graceful stop

Press Ctrl+C at any time. The loop saves state to `history.json` and exits
cleanly. Resume with `--resume`.

---

## Quota management

Google AI Pro gives **15 parallel sessions/day** and **100 total/day**.

| Setup | Sessions used |
|---|---|
| `--parallel 1 --max-iterations 10` | up to 10 |
| `--parallel 3 --max-iterations 8` | up to 24 |
| `--parallel 5 --max-iterations 10` | up to 50 |
| `--parallel 15 --max-iterations 6` | up to 90 (near limit) |

In eval-harness mode, `--parallel N` runs N variants inside **one** Jules
session, so quota usage is always 1 session per iteration regardless of N.

---

## Troubleshooting

**`JULES_API_KEY not set`**
Set `export JULES_API_KEY="..."` or create a `.env` file in the working directory.

**Session `FAILED`**
Check `autoresearch_results/iteration_N/session_ID.json` for the failure reason.
Common causes: missing dependency in the repo, unclear task prompt, Jules hit
a runtime error. Re-run the same iteration after fixing.

**Jules asks a question and loop is stuck**
Go to the URL printed in the terminal output and reply in the Jules web UI.

**Metric never converges**
- Make sure `eval_harness.py` outputs meaningful FP/FN IDs in `details` — Jules
  uses these to understand what's failing
- Try adding more detail to `--target`
- Check that `labels.json` was built from ground truth, not from the classifier

**`HTTP 401` on activities/sendMessage**
Expected — these endpoints require OAuth, not API key. Use the Jules web UI for
interactive steps.

**Session completes but code wasn't pushed**
You forgot `--auto-pr`. Without it, Jules holds the changeset internally but
never pushes a branch. Re-run with `--auto-pr`.

**Second session can't see first session's files**
Jules sessions are branch-scoped. If session 1 pushed to branch `jules/fix-abc`,
pass `--branch jules/fix-abc` to session 2.
