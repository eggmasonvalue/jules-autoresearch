#!/usr/bin/env python3
"""
eval_harness_template.py — Copy this into your repo as scripts/eval_harness.py
and adapt it for your classifier / filter.

This is the script Jules will call repeatedly during the autoresearch loop.
It receives a JSON parameter set, runs the classifier against a labelled
ground truth dataset, and prints a JSON result to stdout.

autoresearch.py invokes it like:
    python scripts/eval_harness.py --params '<json>'

The script MUST:
  - Accept --params as a single-line JSON string
  - Print a JSON object to stdout containing at minimum:
      {"metric_value": <float>, "target_met": <bool>}
  - Exit 0 (even if target not met — exit code is not used for flow control)

The optional "details" field helps Jules understand what's working/failing.

Usage (standalone test):
    python scripts/eval_harness.py --params '{"efts_queries": ["conference"], "exclusions": ["conference call"], "patterns": ["will present at"]}'
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load your labelled ground truth dataset
# ---------------------------------------------------------------------------
# Format: a list of dicts, each with at minimum:
#   {"text": "<filing text>", "label": "CONFERENCE_ATTENDANCE" | "OTHER"}
#
# Build this once manually (~100 labelled filings) and commit to the repo.
# See SKILL.md "Eval Harness Pattern" for guidance.

LABELS_PATH = Path(__file__).parent.parent / "data" / "labels.json"


def load_labels() -> list:
    if not LABELS_PATH.exists():
        print(
            json.dumps({
                "error": f"Labels file not found: {LABELS_PATH}",
                "metric_value": 0.0,
                "target_met": False,
            })
        )
        sys.exit(0)
    with open(LABELS_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Your classifier (adapt this to your actual filter logic)
# ---------------------------------------------------------------------------

def classify(text: str, params: dict) -> bool:
    """
    Return True if `text` is a positive example (e.g. conference attendance).

    Adapt this to match your two-stage filter:
      Stage 1: exclude phrases (cheap reject)
      Stage 2: require at least one attendance verb pattern (confirm positive)

    params keys (example for conference detection):
      efts_queries  - list of EFTS search queries (not used at classify time,
                      used upstream when pulling candidate filings)
      exclusions    - list of phrases that indicate a false positive
      patterns      - list of regex patterns that confirm a true positive
    """
    import re

    text_lower = text.lower()

    # Stage 1: exclusion check
    for excl in params.get("exclusions", []):
        # If the ONLY occurrence of the key signal word is inside an exclusion phrase, reject
        if excl.lower() in text_lower:
            # Check if the text contains anything OTHER than the exclusion phrase
            # (simple heuristic: if "conference" only appears in "conference call", reject)
            signal_word = excl.split()[0].lower()
            all_occurrences = [m.start() for m in re.finditer(re.escape(signal_word), text_lower)]
            excl_occurrences = [m.start() for m in re.finditer(re.escape(excl.lower()), text_lower)]
            if len(all_occurrences) == len(excl_occurrences):
                return False  # all occurrences are inside exclusion phrases

    # Stage 2: attendance verb check
    patterns = params.get("patterns", [])
    if not patterns:
        return True  # no filter = accept all

    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True

    return False


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(params: dict) -> dict:
    """Run classifier against labelled dataset and return metrics."""
    labels = load_labels()

    tp = fp = tn = fn = 0
    errors = []

    for item in labels:
        text = item["text"]
        true_label = item["label"]  # "CONFERENCE_ATTENDANCE" or "OTHER"
        predicted = classify(text, params)
        is_positive = true_label == "CONFERENCE_ATTENDANCE"

        if predicted and is_positive:
            tp += 1
        elif predicted and not is_positive:
            fp += 1
            errors.append({"type": "FP", "id": item.get("id"), "snippet": text[:100]})
        elif not predicted and is_positive:
            fn += 1
            errors.append({"type": "FN", "id": item.get("id"), "snippet": text[:100]})
        else:
            tn += 1

    total = tp + fp + tn + fn
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    # Primary metric: precision (tune for high precision first, then relax for recall)
    # Change this to f1 or recall depending on what you're optimising for
    metric_value = round(precision, 4)

    return {
        "metric_value": metric_value,
        "target_met": metric_value >= 0.90 and recall >= 0.70,  # adjust thresholds
        "details": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "total": total,
            "false_positives": errors[:5],   # first 5 FPs for Jules to analyse
            "false_negatives": errors[-5:],  # last 5 FNs
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Eval harness for autoresearch loop")
    p.add_argument("--params", required=True,
                   help="JSON string of classifier parameters")
    args = p.parse_args()

    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON in --params: {e}", "metric_value": 0.0, "target_met": False}))
        sys.exit(0)

    result = evaluate(params)
    # Print as a single JSON line — Jules reads this from stdout
    print(json.dumps(result))


if __name__ == "__main__":
    main()
