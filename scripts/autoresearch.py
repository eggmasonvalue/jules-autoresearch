#!/usr/bin/env python3
"""
autoresearch.py — Iterative parameter-refinement loop using Jules.

Jules runs the research/eval task in a cloud VM. It evaluates the target
metric, then suggests improved parameters. This script orchestrates the loop:
dispatch → poll → parse → evaluate → refine → repeat.

Two modes:
  research     (default) Jules reasons about the task and reports qualitative
               or quantitative results. Good for open-ended research.

  eval-harness Jules runs a deterministic eval script in the repo against a
               labelled dataset. Use --eval-script to enable this mode.
               Params are passed as structured JSON via --params.

Usage (research mode):
  python autoresearch.py \\
    --source "sources/github-owner-repo" \\
    --task "Search for papers matching: {keywords}" \\
    --keywords "transformer, attention" \\
    --target "find 5 papers with citations > 50" \\
    --metric-type qualitative

Usage (eval-harness mode):
  python autoresearch.py \\
    --source "sources/github-owner-repo" \\
    --eval-script "scripts/eval_harness.py" \\
    --params '{"efts_queries": ["conference"], "exclusions": ["conference call"], "patterns": ["will present at"]}' \\
    --target "precision >= 0.90 with recall >= 0.70" \\
    --metric-type numeric --target-value 0.90

See SKILL.md for full documentation.
"""

import copy
import os
import sys
import json
import time
import signal
import argparse
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

# Add skill scripts dir to path so we can import jules_client
sys.path.insert(0, str(Path(__file__).parent))
from jules_client import JulesClient, _log, _parse_json_block  # noqa: E402


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

# --- Research mode (open-ended reasoning/research) ---
RESEARCH_PROMPT_TEMPLATE = """\
You are performing an autoresearch task. Follow these instructions carefully.

## Your Task

{task_description}

## Current Keywords / Parameters

{keywords}

## Success Target

{target}

## Metric Type

{metric_instructions}

## Instructions

1. Perform the task using the keywords/parameters above.
2. Evaluate whether the success target has been met.
3. At the very end of your final message, output a JSON result block (and ONLY
   this JSON, no other text after it) using EXACTLY this format:

```json
{{
  "metric_value": <number or null>,
  "target_met": <true or false>,
  "confidence": "<low|medium|high>",
  "suggested_keywords": "<improved keyword string for next iteration>",
  "rationale": "<one paragraph: what worked, what didn't, why the suggestion is better>",
  "summary": "<one sentence summary of what you found/did>"
}}
```

IMPORTANT:
- `metric_value`: a number if the metric is numeric, otherwise null
- `target_met`: true ONLY if you are confident the target has been fully met
- `suggested_keywords`: comma-separated string to try next (even if target is met)
- The JSON block must be the very last thing in your message

## Iteration Context

- Iteration number: {iteration}
{history_context}
"""

# --- Eval-harness mode (run a script, measure metric, tune params) ---
EVAL_HARNESS_PROMPT_TEMPLATE = """\
You are running an iterative parameter optimisation loop. Your job this
iteration is to evaluate parameter sets against the eval harness, analyse the
results, and propose the best improvement.

## Eval Harness

Script path in this repo: `{eval_script}`

Run it like this (params as a single-line JSON string):
```bash
python {eval_script} --params '<json>'
```

The script prints a JSON line to stdout with at least:
  {{"metric_value": <float>, "details": {{...}}}}
It exits 0 on success regardless of whether the metric target is met.

## Current Parameters

```json
{params_json}
```

## Success Target

{target}

## Metric

{metric_instructions}

## Iteration Context

- Iteration: {iteration}
{history_context}

## What to do this iteration

{variant_instructions}

## Output format

At the very LAST thing in your final message (nothing after it), output:

```json
{{
  "metric_value": <best metric value achieved this iteration>,
  "target_met": <true or false>,
  "confidence": "<low|medium|high>",
  "suggested_params": <the best-performing or most-promising params as a JSON object>,
  "rationale": "<one paragraph: what the numbers showed, why suggested_params is better>",
  "summary": "<one sentence>",
  "all_results": [
    {{"params": {{...}}, "metric_value": <float>, "label": "baseline"}},
    {{"params": {{...}}, "metric_value": <float>, "label": "variant-1"}},
    ...
  ]
}}
```

- `suggested_params` MUST have the same keys as the input params, with modified values
- `all_results` must contain one entry per variant evaluated
- The JSON block must be the absolute last content in your response
"""

METRIC_INSTRUCTIONS = {
    "numeric": (
        "The metric is a NUMBER. Compute it precisely. "
        "Set `metric_value` to the numeric result. "
        "Set `target_met` to true if metric_value >= {target_value}."
    ),
    "boolean": (
        "The metric is PASS/FAIL. "
        "Set `metric_value` to null. "
        "Set `target_met` to true only if the target condition is fully satisfied."
    ),
    "qualitative": (
        "The metric is QUALITATIVE — use your judgment. "
        "Set `metric_value` to a score from 0.0 to 1.0 (1.0 = perfect). "
        "Set `target_met` to true if score >= 0.8."
    ),
}


# ---------------------------------------------------------------------------
# Variant generation (for structured params + eval-harness parallel mode)
# ---------------------------------------------------------------------------

def generate_param_variants(current_params: Dict, n: int) -> List[Dict]:
    """
    Generate n systematic ablation variants of current_params for parallel
    evaluation within a single Jules session.

    Strategy: for each list-valued key, create a variant that removes the
    last element (ablation test). This tells Jules which elements are load-
    bearing vs. redundant. Variant 0 is always the original unchanged.
    """
    variants = [copy.deepcopy(current_params)]

    if n <= 1:
        return variants

    # Per-list ablations: remove last element from each list key
    for key in sorted(current_params.keys()):
        val = current_params[key]
        if isinstance(val, list) and len(val) > 1:
            v = copy.deepcopy(current_params)
            v[key] = val[:-1]
            variants.append(v)
            if len(variants) >= n:
                return variants[:n]

    # Per-list additions: copy with a placeholder comment (Jules will fill in)
    for key in sorted(current_params.keys()):
        val = current_params[key]
        if isinstance(val, list):
            v = copy.deepcopy(current_params)
            v[key] = val + ["<try adding one more pattern here>"]
            variants.append(v)
            if len(variants) >= n:
                return variants[:n]

    # Pad with copies if we still need more
    while len(variants) < n:
        variants.append(copy.deepcopy(current_params))

    return variants[:n]


def _build_variant_instructions(
    variants: List[Dict],
    eval_script: str,
) -> str:
    """Build the step-by-step eval instructions for Jules in harness mode."""
    lines = []
    for i, v in enumerate(variants):
        label = "baseline" if i == 0 else f"variant-{i}"
        params_str = json.dumps(v)
        lines.append(f"### {label.upper()}: Evaluate these parameters")
        lines.append(f"```bash")
        lines.append(f"python {eval_script} --params '{params_str}'")
        lines.append(f"```")
        lines.append(f"Parameters: `{params_str}`")
        lines.append("")
    lines.append(
        "After running all variants, compare their metric values. "
        "If a variant improved on the baseline, incorporate those changes into "
        "`suggested_params`. Also think about what OTHER changes might help — "
        "add your own improvements in `suggested_params` beyond just picking the "
        "best variant."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State / persistence
# ---------------------------------------------------------------------------

class ResearchState:
    """Tracks the iteration history and persists to disk."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = output_dir / "history.json"
        self.best_path = output_dir / "best.json"
        self.iterations: List[Dict] = []
        self.best_result: Optional[Dict] = None
        self.sessions_used: int = 0

    def record_iteration(
        self,
        iteration: int,
        keywords: str,
        params: Optional[Dict],
        session_ids: List[str],
        result: Optional[Dict],
        raw_outputs: Dict,
    ):
        entry = {
            "iteration": iteration,
            "keywords": keywords,
            "params": params,
            "sessions": session_ids,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metric_value": result.get("metric_value") if result else None,
            "target_met": result.get("target_met", False) if result else False,
            "confidence": result.get("confidence") if result else None,
            # structured params mode
            "suggested_params": result.get("suggested_params") if result else params,
            # flat string mode (backward compat)
            "suggested_keywords": result.get("suggested_keywords", keywords) if result else keywords,
            "all_results": result.get("all_results") if result else None,
            "rationale": result.get("rationale") if result else None,
            "summary": result.get("summary") if result else None,
            "sessions_used_this_iter": len(session_ids),
        }
        self.iterations.append(entry)
        self.sessions_used += len(session_ids)

        # Track best result
        if result and result.get("target_met"):
            self.best_result = entry
        elif result and (
            self.best_result is None
            or _compare_metric(result.get("metric_value"), self.best_result.get("metric_value"))
        ):
            self.best_result = entry

        self._save()

        # Save per-iteration session outputs
        iter_dir = self.output_dir / f"iteration_{iteration}"
        iter_dir.mkdir(exist_ok=True)
        for sid in session_ids:
            out_file = iter_dir / f"session_{sid}.json"
            with open(out_file, "w") as f:
                json.dump(raw_outputs.get(sid, {}), f, indent=2)

    def _save(self):
        last = self.iterations[-1] if self.iterations else {}
        data = {
            "iterations": self.iterations,
            "final_keywords": last.get("suggested_keywords", ""),
            "final_params": last.get("suggested_params"),
            "target_met": any(i["target_met"] for i in self.iterations),
            "total_sessions_used": self.sessions_used,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        with open(self.history_path, "w") as f:
            json.dump(data, f, indent=2)
        if self.best_result:
            with open(self.best_path, "w") as f:
                json.dump(self.best_result, f, indent=2)

    def load_resume(self) -> Optional[Tuple[int, str, Optional[Dict]]]:
        """Return (last_iteration, last_keywords, last_params) if history exists."""
        if self.history_path.exists():
            with open(self.history_path) as f:
                data = json.load(f)
            iters = data.get("iterations", [])
            if iters:
                last = iters[-1]
                kw = last.get("suggested_keywords", "")
                params = last.get("suggested_params")
                return last["iteration"], kw, params
        return None

    def history_context_for_prompt(self, last_n: int = 3) -> str:
        """Summarise recent iteration history for Jules' context."""
        if not self.iterations:
            return "- This is the first iteration. No prior history."
        recent = self.iterations[-last_n:]
        lines = []
        for entry in recent:
            mv = entry.get("metric_value")
            tm = entry.get("target_met", False)
            rat = (entry.get("rationale") or "")[:300]
            # Show params if available, otherwise keywords
            if entry.get("params"):
                param_str = json.dumps(entry["params"])[:120]
                lines.append(
                    f"- Iter {entry['iteration']}: params={param_str}, "
                    f"metric={mv}, target_met={tm}. Rationale: {rat}"
                )
            else:
                kw = entry.get("keywords", "")
                lines.append(
                    f"- Iter {entry['iteration']}: keywords='{kw}', "
                    f"metric={mv}, target_met={tm}. Rationale: {rat}"
                )
        return "\n".join(lines)


def _compare_metric(new_val, best_val) -> bool:
    """Return True if new_val is better than best_val (higher is better)."""
    if new_val is None:
        return False
    if best_val is None:
        return True
    try:
        return float(new_val) > float(best_val)
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_prompt(
    task_description: str,
    keywords: str,
    params: Optional[Dict],
    target: str,
    metric_type: str,
    target_value: Optional[float],
    iteration: int,
    state: "ResearchState",
    eval_script: Optional[str] = None,
    n_variants: int = 1,
) -> str:
    """Build the Jules prompt for this iteration."""
    metric_instr = METRIC_INSTRUCTIONS.get(metric_type, METRIC_INSTRUCTIONS["qualitative"])
    if target_value is not None:
        metric_instr = metric_instr.format(target_value=target_value)
    history_ctx = state.history_context_for_prompt()

    # --- Eval-harness mode ---
    if eval_script and params is not None:
        variants = generate_param_variants(params, n_variants)
        variant_instructions = _build_variant_instructions(variants, eval_script)
        params_json = json.dumps(params, indent=2)
        return EVAL_HARNESS_PROMPT_TEMPLATE.format(
            eval_script=eval_script,
            params_json=params_json,
            target=target,
            metric_instructions=metric_instr,
            iteration=iteration,
            history_context=history_ctx,
            variant_instructions=variant_instructions,
        )

    # --- Research mode ---
    task_with_keywords = task_description.replace("{keywords}", keywords)
    return RESEARCH_PROMPT_TEMPLATE.format(
        task_description=task_with_keywords,
        keywords=keywords,
        target=target,
        metric_instructions=metric_instr,
        iteration=iteration,
        history_context=history_ctx,
    )


# ---------------------------------------------------------------------------
# Session dispatch + polling
# ---------------------------------------------------------------------------

def run_parallel_sessions(
    client: JulesClient,
    prompts: List[str],
    source: Optional[str],
    branch: str,
    title_prefix: str,
    require_plan_approval: bool,
    auto_pr: bool,
    poll_interval: int,
    timeout: int,
) -> List[Dict]:
    """
    Dispatch N sessions simultaneously, poll all until done.
    Returns completed session dicts in the same order as prompts.
    """
    automation_mode = "AUTO_CREATE_PR" if auto_pr else None

    sessions = []
    for i, prompt in enumerate(prompts):
        title = f"{title_prefix} (variant {i+1})" if len(prompts) > 1 else title_prefix
        _log(f"  Dispatching session {i+1}/{len(prompts)}...")
        session = client.create_session(
            prompt=prompt,
            source=source,
            branch=branch,
            title=title,
            require_plan_approval=require_plan_approval,
            automation_mode=automation_mode,
        )
        sessions.append(session)
        sid = session.get("id") or session.get("name", "?")
        _log(f"  ✓ Session {sid} queued — {session.get('url', '')}")
        if i < len(prompts) - 1:
            time.sleep(1)

    session_ids = [s.get("id") or s.get("name", "").split("/")[-1] for s in sessions]
    completed = {}
    pending = set(session_ids)

    _log(f"\n  Waiting for {len(pending)} session(s) to complete...")
    start = time.time()
    _feedback_shown: set = set()  # sessions whose AWAITING_USER_FEEDBACK we've already printed

    while pending:
        for sid in list(pending):
            session = client.get_session(sid)
            state = session.get("state", "")
            if state in {"COMPLETED", "FAILED"}:
                pending.discard(sid)
                completed[sid] = session
                icon = "✓" if state == "COMPLETED" else "✗"
                _log(f"  {icon} Session {sid} → {state}")
            elif state == "AWAITING_PLAN_APPROVAL" and not require_plan_approval:
                _log(f"  Auto-approving plan for {sid}...")
                client.approve_plan(sid)
            elif state == "AWAITING_USER_FEEDBACK":
                if sid not in _feedback_shown:
                    question = client.get_latest_agent_message(sid)
                    session_url = client.get_session(sid).get("url", f"https://jules.google.com/session/{sid}")
                    _log("")
                    _log("  " + "="*60)
                    _log(f"  JULES HAS A QUESTION (session {sid})")
                    _log("  " + "="*60)
                    if question:
                        for line in question.splitlines():
                            _log(f"  {line}")
                    _log("  " + "-"*60)
                    _log(f"  Respond at: {session_url}")
                    _log("  Polling resumes automatically once you reply.")
                    _log("  " + "="*60)
                    _log("")
                    _feedback_shown.add(sid)

        if pending:
            elapsed = time.time() - start
            if elapsed > timeout:
                raise TimeoutError(
                    f"{len(pending)} session(s) timed out after {elapsed:.0f}s: {pending}"
                )
            _log(f"  {len(pending)} session(s) still running... (next check in {poll_interval}s)")
            time.sleep(poll_interval)

    return [completed.get(sid, {}) for sid in session_ids]


def pick_best_result(
    session_ids: List[str],
    results: List[Optional[Dict]],
) -> Tuple[Optional[Dict], str]:
    """Pick the best result from parallel sessions. Returns (result, session_id)."""
    for sid, r in zip(session_ids, results):
        if r and r.get("target_met"):
            return r, sid  # first target-met wins

    scored = [
        (r.get("metric_value"), sid, r)
        for sid, r in zip(session_ids, results)
        if r is not None
    ]
    if not scored:
        return None, session_ids[0] if session_ids else ""

    scored.sort(key=lambda x: float(x[0]) if x[0] is not None else -1.0, reverse=True)
    _, best_sid, best_r = scored[0]
    return best_r, best_sid


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def autoresearch_loop(args) -> dict:
    """Main autoresearch loop."""
    client = JulesClient(api_key=args.api_key)
    output_dir = Path(args.output_dir)
    state = ResearchState(output_dir)

    # --- Parse structured params if provided ---
    current_params: Optional[Dict] = None
    if args.params:
        raw = args.params
        if raw.startswith("@"):
            with open(raw[1:]) as f:
                current_params = json.load(f)
        else:
            current_params = json.loads(raw)

    eval_script = args.eval_script  # None in research mode

    # Derive display keywords from params if needed
    if current_params is not None and not args.keywords:
        current_keywords = json.dumps(current_params)
    else:
        current_keywords = args.keywords or ""

    # n_variants: how many param sets Jules evaluates per iteration
    # In eval-harness mode: Jules runs all variants in ONE session (saves quota)
    # In research mode: we dispatch one session per variant
    n_variants = args.parallel if eval_script else 1

    # --- Resume ---
    start_iteration = 1
    if args.resume:
        resume_data = state.load_resume()
        if resume_data:
            start_iteration, resume_kw, resume_params = resume_data
            start_iteration += 1
            if resume_params is not None:
                current_params = resume_params
                current_keywords = json.dumps(current_params)
            else:
                current_keywords = resume_kw
            _log(f"Resuming from iteration {start_iteration}")

    mode = "eval-harness" if eval_script else "research"
    _log(f"\n{'='*60}")
    _log(f"AUTORESEARCH LOOP  [{mode} mode]")
    if current_params:
        _log(f"  Params: {json.dumps(current_params)[:120]}")
    else:
        _log(f"  Keywords: {current_keywords}")
    _log(f"  Target: {args.target}")
    _log(f"  Metric: {args.metric_type}" + (f" >= {args.target_value}" if args.target_value else ""))
    if eval_script:
        _log(f"  Eval script: {eval_script}  (variants/iter: {n_variants})")
    _log(f"  Max iterations: {args.max_iterations}  |  Parallel sessions: {args.parallel}")
    _log(f"  Output: {output_dir.resolve()}")
    _log(f"{'='*60}\n")

    interrupted = [False]
    def _handle_interrupt(sig, frame):
        interrupted[0] = True
        _log("\n⚠ Interrupted — saving state and exiting after this iteration...")
    signal.signal(signal.SIGINT, _handle_interrupt)

    for iteration in range(start_iteration, args.max_iterations + 1):
        _log(f"\n[Iteration {iteration}/{args.max_iterations}]")
        if current_params:
            _log(f"  Params: {json.dumps(current_params)[:120]}")
        else:
            _log(f"  Keywords: {current_keywords}")

        prompt = build_prompt(
            task_description=args.task or "",
            keywords=current_keywords,
            params=current_params,
            target=args.target,
            metric_type=args.metric_type,
            target_value=args.target_value,
            iteration=iteration,
            state=state,
            eval_script=eval_script,
            n_variants=n_variants,
        )

        # In eval-harness mode: 1 session runs all variants internally (quota-efficient).
        # In research mode: N sessions run in parallel.
        if eval_script:
            prompts = [prompt]  # Jules handles all variants in one session
        else:
            prompts = [prompt]
            if args.parallel > 1:
                for var_i in range(1, args.parallel):
                    prompts.append(
                        prompt + f"\n\n[VARIANT {var_i+1}/{args.parallel}]: "
                        "Explore a meaningfully different keyword/approach than variant 1."
                    )

        title_prefix = f"Autoresearch iter {iteration}"
        try:
            completed_sessions = run_parallel_sessions(
                client=client,
                prompts=prompts,
                source=args.source,
                branch=args.branch,
                title_prefix=title_prefix,
                require_plan_approval=args.require_plan_approval,
                auto_pr=args.auto_pr,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
            )
        except TimeoutError as e:
            _log(f"  ✗ Timeout: {e}")
            break

        # Extract results
        session_ids = []
        all_results = []
        raw_outputs: dict = {}

        for sess in completed_sessions:
            sid = sess.get("id") or sess.get("name", "?").split("/")[-1]
            session_ids.append(sid)
            outputs = client.extract_outputs(sid)
            raw_outputs[sid] = {"session": sess, "outputs": outputs}
            result = client.extract_result_json(sid)
            if result is None and outputs["agent_messages"]:
                result = _parse_json_block(outputs["agent_messages"][-1])
            if result is None:
                _log(f"  ⚠ Could not parse result JSON from session {sid}")
                last_msg = (outputs["agent_messages"] or ["(none)"])[-1]
                _log(f"    Last message preview: {last_msg[:400]}")
            all_results.append(result)

        best_result, best_sid = pick_best_result(session_ids, all_results)

        # Log outcome
        if best_result:
            mv = best_result.get("metric_value")
            tm = best_result.get("target_met", False)
            _log(f"  metric_value={mv}  target_met={tm}  confidence={best_result.get('confidence')}")
            _log(f"  Summary: {best_result.get('summary', '')}")
            # In eval-harness mode, log all_results sub-table if present
            if eval_script and best_result.get("all_results"):
                for r in best_result["all_results"]:
                    _log(f"    [{r.get('label','?')}] metric={r.get('metric_value')} "
                         f"params={json.dumps(r.get('params',{}))[:80]}")
        else:
            tm = False
            _log("  No structured result returned by Jules")

        # Advance params/keywords for next iteration
        if best_result:
            if eval_script and best_result.get("suggested_params") is not None:
                next_params = best_result["suggested_params"]
                next_keywords = json.dumps(next_params)
            else:
                next_params = best_result.get("suggested_params", current_params)
                next_keywords = best_result.get("suggested_keywords", current_keywords)
        else:
            next_params = current_params
            next_keywords = current_keywords

        state.record_iteration(
            iteration=iteration,
            keywords=current_keywords,
            params=current_params,
            session_ids=session_ids,
            result=best_result,
            raw_outputs=raw_outputs,
        )

        if tm:
            _log(f"\n🎯 TARGET MET on iteration {iteration}!")
            if current_params:
                _log(f"   Final params: {json.dumps(current_params)}")
            else:
                _log(f"   Final keywords: {current_keywords}")
            _log(f"   Results: {output_dir.resolve()}")
            break

        if iteration == args.max_iterations:
            _log(f"\n⚠ Max iterations ({args.max_iterations}) reached without meeting target.")
            if state.best_result:
                br = state.best_result
                _log(f"  Best was iteration {br['iteration']}, metric={br.get('metric_value')}")
                if br.get("params"):
                    _log(f"  Best params: {json.dumps(br['params'])}")
            break

        if interrupted[0]:
            _log("  Stopped by user.")
            break

        current_params = next_params
        current_keywords = next_keywords
        _log(f"\n  → Next: {current_keywords[:120]}")

    _log(f"\n{'='*60}")
    _log("AUTORESEARCH COMPLETE")
    _log(f"  Iterations run:  {len(state.iterations)}")
    _log(f"  Sessions used:   {state.sessions_used}")
    _log(f"  History:         {state.history_path}")
    _log(f"  Best result:     {state.best_path}")
    _log(f"{'='*60}\n")

    if state.best_result:
        print(json.dumps(state.best_result, indent=2))

    return {
        "iterations": state.iterations,
        "target_met": any(i["target_met"] for i in state.iterations),
        "sessions_used": state.sessions_used,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Autoresearch loop using Jules as compute engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Research mode (open-ended)
              python autoresearch.py \\
                --source "sources/github-myuser-sandbox" \\
                --task "Find papers about: {keywords}" \\
                --keywords "transformers, attention" \\
                --target "5 papers with >50 citations from 2022+" \\
                --metric-type qualitative

              # Eval-harness mode (structured params + deterministic eval script)
              python autoresearch.py \\
                --source "sources/github-myuser-repo" \\
                --eval-script "scripts/eval_harness.py" \\
                --params '{"efts_queries": ["conference", "fireside chat"], "exclusions": ["conference call"], "patterns": ["will present at", "presenting at"]}' \\
                --target "precision >= 0.90 with recall >= 0.70" \\
                --metric-type numeric --target-value 0.90 \\
                --parallel 3
        """),
    )

    # Task / keywords / params
    parser.add_argument("--task",
        help="Task description for Jules. Use {keywords} as placeholder. "
             "Optional in eval-harness mode (--eval-script).")
    parser.add_argument("--keywords",
        help="Initial keyword string (research mode). "
             "Optional when --params is set.")
    parser.add_argument("--params",
        help="Initial parameters as a JSON string or @path/to/file.json "
             "(eval-harness mode). E.g. '{\"efts_queries\": [\"conference\"]}'"
    )
    parser.add_argument("--target", required=True,
        help="Natural language description of the success target.")

    # Eval harness
    parser.add_argument("--eval-script",
        help="Path to the eval harness script in the repo "
             "(e.g. scripts/eval_harness.py). Enables eval-harness mode.")

    # Source / repo
    parser.add_argument("--source",
        help="Jules source name (e.g. 'sources/github-owner-repo'). "
             "Get from: python jules_client.py list-sources")
    parser.add_argument("--branch", default="main",
        help="Starting branch [default: main]")

    # Metric
    parser.add_argument("--metric-type",
        choices=["numeric", "boolean", "qualitative"],
        default="qualitative",
        help="How to evaluate the metric [default: qualitative]")
    parser.add_argument("--target-value", type=float,
        help="Threshold for numeric metrics (e.g. 0.90)")

    # Loop control
    parser.add_argument("--max-iterations", type=int, default=10,
        help="Max iterations before stopping [default: 10]")
    parser.add_argument("--parallel", type=int, default=1,
        help="In eval-harness mode: variants Jules evaluates per iteration. "
             "In research mode: parallel Jules sessions. [default: 1, max: 15]")
    parser.add_argument("--poll-interval", type=int, default=20,
        help="Seconds between status checks [default: 20]")
    parser.add_argument("--timeout", type=int, default=3600,
        help="Session timeout in seconds [default: 3600]")

    # Output / state
    parser.add_argument("--output-dir", default="autoresearch_results",
        help="Directory to save results [default: autoresearch_results]")
    parser.add_argument("--resume", action="store_true",
        help="Resume from existing history.json in --output-dir")

    # Session options
    parser.add_argument("--require-plan-approval", action="store_true",
        help="Pause for plan review before Jules executes")
    parser.add_argument("--auto-pr", action="store_true",
        help="Auto-create GitHub PR for code changes")

    # Auth
    parser.add_argument("--api-key",
        help="Jules API key (overrides JULES_API_KEY env var)")

    args = parser.parse_args()

    # Validation
    if not args.keywords and not args.params:
        parser.error("Provide at least one of --keywords or --params")
    if args.metric_type == "numeric" and args.target_value is None:
        parser.error("--target-value is required when --metric-type is numeric")
    if args.parallel < 1 or args.parallel > 15:
        parser.error("--parallel must be between 1 and 15")
    if args.eval_script and not args.params and not args.keywords:
        parser.error("--eval-script requires --params (or --keywords for fallback)")

    try:
        autoresearch_loop(args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        _log("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
