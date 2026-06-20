# Eval Harness Pattern

Use this when your metric can be computed deterministically from code (precision/recall/F1, benchmark scores, test pass rates). It's more accurate, faster to converge, and removes qualitative guesswork.

## What you need in your repo

```
your-repo/
├── data/
│   └── labels.json          # Ground truth — labelled examples
├── scripts/
│   ├── classifier.py        # The thing you're tuning (accepts --params)
│   └── eval_harness.py      # Runs classifier on labels, reports metrics
└── ...
```

## labels.json format

```json
[
  {"id": "acc-001", "text": "<full text to classify>", "label": "POSITIVE_CLASS"},
  {"id": "acc-002", "text": "<full text to classify>", "label": "OTHER"},
  ...
]
```

**Build this from ground truth, not from the classifier.** If you use the classifier to auto-label, you'll get self-referential labels where recall is unmeasurably high (all false negatives end up in the "OTHER" bucket). Label using the most liberal possible heuristic, then have a human spot-check.

Aim for 150-200 examples, ~40-60% positive, drawn from at least two different time periods or contexts for diversity.

## eval_harness.py contract

See `eval_harness_template.py` in this directory for a full copy-paste starting point.

The script **must**:
- Accept `--params '<json>'` as a CLI argument
- Print one JSON line to stdout: `{"metric_value": <float>, "target_met": <bool>, "details": {...}}`
- Exit 0 always (even if target not met — exit code is not used for flow control)

The `details` field drives Jules' suggestions — include false positive and false negative IDs so Jules can see which cases are failing.

## Minimal eval_harness.py

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
    fps, fns = [], []
    for item in labels:
        pred = classify(item["text"], params)
        pos  = item["label"] == "POSITIVE_CLASS"
        if pred and pos:          tp += 1
        elif pred and not pos:    fp += 1; fps.append(item["id"])
        elif not pred and pos:    fn += 1; fns.append(item["id"])
        else:                     tn += 1

    prec = tp / (tp + fp) if tp + fp else 0.0
    rec  = tp / (tp + fn) if tp + fn else 0.0
    print(json.dumps({
        "metric_value": round(prec, 4),
        "target_met": prec >= 0.90 and rec >= 0.70,
        "details": {
            "precision": prec, "recall": rec,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "false_positive_ids": fps[:5],
            "false_negative_ids": fns[:5],
        }
    }))

if __name__ == "__main__":
    main()
```

## Designing good tasks

**Research mode** — the task prompt should:
1. Use `{keywords}` as the placeholder for the current keyword set
2. Tell Jules exactly what metric to compute and how
3. Be specific — avoid ambiguous multi-step tasks

**Eval-harness mode** — the task is defined by the eval script. You don't need `--task`; Jules already knows to run `--eval-script`. Focus your energy on making `labels.json` comprehensive and `eval_harness.py` fast.

**Good eval-harness prompt:**
> "Run `python scripts/eval_harness.py --params '<json>'` with the current parameters. Analyse the false positive and false negative IDs in the output. Propose a new parameter set that improves precision without dropping recall below 0.70."

**Less good:**
> "Try to improve the classifier somehow."
