#!/usr/bin/env python3
"""
Jules REST API client.
Pure stdlib — no external dependencies.

Usage as a CLI:
  python jules_client.py list-sources
  python jules_client.py create-session --source "sources/github-owner-repo" --prompt "Fix the bug"
  python jules_client.py get-session SESSION_ID
  python jules_client.py wait SESSION_ID
  python jules_client.py list-sessions
  python jules_client.py send-message SESSION_ID "message"
  python jules_client.py approve-plan SESSION_ID
  python jules_client.py delete-session SESSION_ID
  python jules_client.py list-activities SESSION_ID

API key: set JULES_API_KEY env var, or pass --api-key / create a .env file.
"""

import os
import sys
import time
import json
import argparse
import urllib.request
import urllib.error
import urllib.parse
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

BASE_URL = "https://jules.googleapis.com/v1alpha"
TERMINAL_STATES = {"COMPLETED", "FAILED"}
WAITING_STATES = {"AWAITING_PLAN_APPROVAL", "AWAITING_USER_FEEDBACK"}


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------

def _load_dotenv():
    """Load .env from CWD or parent dirs (simple implementation)."""
    current = Path.cwd()
    for path in [current, *current.parents]:
        dotenv = path / ".env"
        if dotenv.exists():
            with open(dotenv) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _make_request(method: str, path: str, api_key: str, body=None) -> Dict[str, Any]:
    """Make a Jules REST API request. Returns parsed JSON response."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {e.code} {e.reason} for {method} {url}\n{body_text}"
        ) from None


# ---------------------------------------------------------------------------
# JulesClient class
# ---------------------------------------------------------------------------

class JulesClient:
    """Wrapper around the Jules REST API v1alpha."""

    def __init__(self, api_key: Optional[str] = None):
        _load_dotenv()
        self.api_key = api_key or os.environ.get("JULES_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Jules API key not found. Set the JULES_API_KEY environment "
                "variable or pass api_key= to JulesClient()."
            )

    def _req(self, method: str, path: str, body=None) -> dict:
        return _make_request(method, path, self.api_key, body)

    # -- Sources --

    def list_sources(self, page_size: int = 100) -> Dict[str, Any]:
        """List all connected GitHub repos (sources)."""
        return self._req("GET", f"/sources?pageSize={page_size}")

    def get_source(self, source_id: str) -> Dict[str, Any]:
        """Get a specific source. source_id can be full name or just the ID part."""
        name = source_id if "/" in source_id else f"sources/{source_id}"
        return self._req("GET", f"/{name}")

    # -- Sessions --

    def create_session(
        self,
        prompt: str,
        source: Optional[str] = None,
        branch: str = "main",
        title: Optional[str] = None,
        require_plan_approval: bool = False,
        automation_mode: Optional[str] = None,  # "AUTO_CREATE_PR" or None
    ) -> Dict[str, Any]:
        """Create a new Jules session."""
        body: dict = {"prompt": prompt}
        if title:
            body["title"] = title
        if source:
            body["sourceContext"] = {
                "source": source,
                "githubRepoContext": {"startingBranch": branch},
            }
        if require_plan_approval:
            body["requirePlanApproval"] = True
        if automation_mode:
            body["automationMode"] = automation_mode
        return self._req("POST", "/sessions", body)

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Retrieve a session by its ID (numeric string or full name)."""
        sid = _extract_session_id(session_id)
        return self._req("GET", f"/sessions/{sid}")

    def list_sessions(self, page_size: int = 30) -> Dict[str, Any]:
        """List all sessions for the authenticated user."""
        return self._req("GET", f"/sessions?pageSize={page_size}")

    def delete_session(self, session_id: str) -> Dict[str, Any]:
        """
        Attempt to delete a session.
        NOTE: The official Google developers API docs (developers.google.com/jules/api)
        do not list a delete endpoint. This may 404. The jules.google.com docs mention
        it, but treat this method as best-effort.
        """
        sid = _extract_session_id(session_id)
        return self._req("DELETE", f"/sessions/{sid}")

    def send_message(self, session_id: str, message: str) -> Dict[str, Any]:
        """Send a message to an active session (when it's waiting for user input)."""
        sid = _extract_session_id(session_id)
        return self._req("POST", f"/sessions/{sid}:sendMessage", {"prompt": message})

    def approve_plan(self, session_id: str) -> Dict[str, Any]:
        """Approve a pending plan (when requirePlanApproval=True)."""
        sid = _extract_session_id(session_id)
        return self._req("POST", f"/sessions/{sid}:approvePlan", {})

    # -- Activities --

    def list_activities(self, session_id: str, page_size: int = 100) -> Dict[str, Any]:
        """
        List all activities for a session, following pagination automatically.
        The API caps pageSize at 100; this method fetches all pages and merges
        them so callers always get the complete activity list.
        """
        sid = _extract_session_id(session_id)
        all_activities = []
        page_token = None

        while True:
            params = f"pageSize={min(page_size, 100)}"
            if page_token:
                params += f"&pageToken={page_token}"
            resp = self._req("GET", f"/sessions/{sid}/activities?{params}")
            all_activities.extend(resp.get("activities", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return {"activities": all_activities}

    def get_activity(self, session_id: str, activity_id: str) -> Dict[str, Any]:
        sid = _extract_session_id(session_id)
        return self._req("GET", f"/sessions/{sid}/activities/{activity_id}")

    # -- High-level helpers --

    def get_latest_agent_message(self, session_id: str) -> Optional[str]:
        """Return the most recent message Jules sent in the session, or None."""
        try:
            activities = self.list_activities(session_id).get("activities", [])
            for act in reversed(activities):
                if "agentMessaged" in act:
                    return act["agentMessaged"].get("agentMessage", "")
        except Exception:
            pass
        return None

    def wait_for_completion(
        self,
        session_id: str,
        poll_interval: int = 20,
        timeout: int = 3600,
        on_progress=None,
        auto_approve_plan: bool = True,
    ) -> Dict[str, Any]:
        """
        Poll a session until it reaches COMPLETED or FAILED.

        When Jules enters AWAITING_USER_FEEDBACK, this method fetches Jules'
        question, prints it in full, and prints the session URL so the user
        can respond via the Jules web UI or CLI. It then keeps polling until
        Jules continues.

        Note: sendMessage via API key is blocked by Google auth (requires
        OAuth). Responses must be sent via https://jules.google.com or the
        jules CLI TUI.
        """
        sid = _extract_session_id(session_id)
        start = time.time()
        last_state = None
        feedback_shown_for_state = False  # avoid reprinting same question every poll

        while True:
            session = self.get_session(sid)
            state = session.get("state", "STATE_UNSPECIFIED")
            url   = session.get("url", f"https://jules.google.com/session/{sid}")

            if state != last_state:
                _log(f"  Session {sid}: {last_state or 'START'} → {state}")
                last_state = state
                feedback_shown_for_state = False  # reset on any state change

            if on_progress:
                on_progress(session)

            if state in TERMINAL_STATES:
                return session

            if state == "AWAITING_PLAN_APPROVAL" and auto_approve_plan:
                _log(f"  Auto-approving plan for session {sid}...")
                self.approve_plan(sid)

            elif state == "AWAITING_USER_FEEDBACK":
                if not feedback_shown_for_state:
                    question = self.get_latest_agent_message(sid)
                    _log("")
                    _log("  " + "="*60)
                    _log(f"  JULES HAS A QUESTION (session {sid})")
                    _log("  " + "="*60)
                    if question:
                        for line in question.splitlines():
                            _log(f"  {line}")
                    else:
                        _log("  (could not retrieve message)")
                    _log("  " + "-"*60)
                    _log(f"  Respond at: {url}")
                    _log("  Polling will resume automatically once you reply.")
                    _log("  " + "="*60)
                    _log("")
                    feedback_shown_for_state = True
                else:
                    _log(f"  Still awaiting your reply at: {url}")

            elapsed = time.time() - start
            if elapsed > timeout:
                raise TimeoutError(
                    f"Session {sid} timed out after {elapsed:.0f}s (state: {state})"
                )

            time.sleep(poll_interval)

    def extract_outputs(self, session_id: str) -> Dict[str, Any]:
        """
        Extract all useful content from a session's activities.

        Returns a dict with:
          agent_messages: list of strings Jules sent
          bash_outputs: list of {command, output, exitCode}
          code_changes: list of {source, gitPatch}
          progress_updates: list of {title, description}
          plan: plan dict if one was generated
          error: failure reason if session failed
        """
        sid = _extract_session_id(session_id)
        resp = self.list_activities(sid)
        activities = resp.get("activities", [])

        result: Dict[str, Any] = {
            "agent_messages": [],
            "bash_outputs": [],
            "code_changes": [],
            "progress_updates": [],
            "plan": None,
            "error": None,
        }

        for act in activities:
            if "agentMessaged" in act:
                result["agent_messages"].append(act["agentMessaged"]["agentMessage"])
            if "progressUpdated" in act:
                result["progress_updates"].append(act["progressUpdated"])
            if "planGenerated" in act:
                result["plan"] = act["planGenerated"]["plan"]
            if "sessionFailed" in act:
                result["error"] = act["sessionFailed"].get("reason", "unknown error")
            for artifact in act.get("artifacts", []):
                if "bashOutput" in artifact:
                    result["bash_outputs"].append(artifact["bashOutput"])
                if "changeSet" in artifact:
                    result["code_changes"].append(artifact["changeSet"])

        return result

    def extract_result_json(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Parse the structured autoresearch result JSON block from Jules' output.
        Searches in priority order:
          1. Agent messages (last to first) — Jules wrote it in its final reply
          2. Bash outputs (last to first) — eval harness printed it to stdout
        Returns the parsed dict, or None if not found.
        """
        outputs = self.extract_outputs(session_id)

        # 1. Agent messages (primary — Jules writes analysis + result JSON here)
        for msg in reversed(outputs["agent_messages"]):
            parsed = _parse_json_block(msg)
            if parsed and "target_met" in parsed:
                return parsed

        # 2. Bash outputs (fallback — eval harness may print result JSON directly)
        for bout in reversed(outputs["bash_outputs"]):
            output_text = bout.get("output", "")
            if output_text:
                parsed = _parse_json_block(output_text)
                if parsed and "target_met" in parsed:
                    return parsed

        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_session_id(session_id: str) -> str:
    """Accept 'sessions/12345' or just '12345'."""
    if session_id.startswith("sessions/"):
        return session_id[len("sessions/"):]
    return session_id


def _parse_json_block(text: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from a markdown code block or raw text."""
    # Try ```json ... ``` blocks first
    pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
    m = re.search(pattern, text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try any JSON object in the text
    for m in re.finditer(r"\{[^{}]*\"target_met\"[^{}]*\}", text, re.DOTALL):
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # Broader search: find the last { ... } block
    brace_start = text.rfind("{")
    if brace_start != -1:
        try:
            candidate = text[brace_start:]
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    return None


def _log(msg: str):
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_list_sources(client: JulesClient, _args):
    resp = client.list_sources()
    sources = resp.get("sources", [])
    if not sources:
        print("No sources connected. Connect a GitHub repo at jules.google.com → Settings → Sources.")
        return
    print(f"{'SOURCE NAME':<45} {'OWNER':<20} {'REPO':<25} {'DEFAULT BRANCH'}")
    print("-" * 110)
    for s in sources:
        gr = s.get("githubRepo", {})
        db = gr.get("defaultBranch", {}).get("displayName", "?")
        print(f"{s['name']:<45} {gr.get('owner',''):<20} {gr.get('repo',''):<25} {db}")


def _cmd_create_session(client: JulesClient, args):
    session = client.create_session(
        prompt=args.prompt,
        source=args.source,
        branch=args.branch,
        title=args.title,
        require_plan_approval=args.require_plan_approval,
        automation_mode="AUTO_CREATE_PR" if args.auto_pr else None,
    )
    print(json.dumps(session, indent=2))
    print(f"\n✓ Session created: {session.get('name')}")
    print(f"  URL: {session.get('url', 'N/A')}")


def _cmd_get_session(client: JulesClient, args):
    session = client.get_session(args.session_id)
    print(json.dumps(session, indent=2))


def _cmd_list_sessions(client: JulesClient, args):
    resp = client.list_sessions(page_size=args.limit)
    sessions = resp.get("sessions", [])
    if not sessions:
        print("No sessions found.")
        return
    print(f"{'ID':<12} {'STATE':<28} {'TITLE':<45} {'CREATED'}")
    print("-" * 110)
    for s in sessions:
        sid = s.get("id", "?")
        state = s.get("state", "?")
        title = (s.get("title") or "")[:43]
        created = (s.get("createTime") or "")[:19]
        print(f"{sid:<12} {state:<28} {title:<45} {created}")


def _cmd_wait(client: JulesClient, args):
    print(f"Polling session {args.session_id}...")
    print(f"Note: if Jules asks a question, respond at https://jules.google.com/session/{args.session_id}")
    print(f"      (sendMessage requires OAuth — API key auth is not sufficient)")
    session = client.wait_for_completion(
        args.session_id,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
        auto_approve_plan=not args.no_auto_approve,
    )
    state = session.get("state")
    print(f"\n{'✓' if state == 'COMPLETED' else '✗'} Session {args.session_id} finished: {state}")
    outputs = client.extract_outputs(args.session_id)
    if outputs["error"]:
        print(f"  Error: {outputs['error']}")
    if outputs["agent_messages"]:
        print(f"\n  Final message from Jules:\n")
        print("  " + "\n  ".join(outputs["agent_messages"][-1].splitlines()))
    if outputs["bash_outputs"]:
        print(f"\n  Bash outputs ({len(outputs['bash_outputs'])} commands):")
        for b in outputs["bash_outputs"][-3:]:  # last 3
            print(f"    $ {b.get('command', '?')}")
            print(f"      exit={b.get('exitCode')} | {(b.get('output','')[:200])!r}")


def _cmd_list_activities(client: JulesClient, args):
    resp = client.list_activities(args.session_id)
    activities = resp.get("activities", [])
    print(f"{len(activities)} activities for session {args.session_id}:\n")
    for act in activities:
        ts = (act.get("createTime") or "")[:19]
        orig = act.get("originator", "?")
        desc = act.get("description", "")
        print(f"  [{ts}] [{orig}] {desc}")
        for artifact in act.get("artifacts", []):
            if "bashOutput" in artifact:
                b = artifact["bashOutput"]
                print(f"    → bash: $ {b.get('command','?')} (exit {b.get('exitCode')})")
            if "changeSet" in artifact:
                print(f"    → code change (patch available)")
            if "media" in artifact:
                print(f"    → media: {artifact['media'].get('mimeType','?')}")
        if "agentMessaged" in act:
            msg = act["agentMessaged"]["agentMessage"][:300]
            print(f"    → Jules: {msg!r}")
        if "sessionFailed" in act:
            print(f"    → FAILED: {act['sessionFailed'].get('reason','?')}")


def _cmd_send_message(client: JulesClient, args):
    client.send_message(args.session_id, args.message)
    print(f"✓ Message sent to session {args.session_id}")


def _cmd_approve_plan(client: JulesClient, args):
    client.approve_plan(args.session_id)
    print(f"✓ Plan approved for session {args.session_id}")


def _cmd_delete_session(client: JulesClient, args):
    client.delete_session(args.session_id)
    print(f"✓ Session {args.session_id} deleted")


def main():
    parser = argparse.ArgumentParser(
        description="Jules REST API client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--api-key", help="Jules API key (overrides JULES_API_KEY env var)")
    sub = parser.add_subparsers(dest="command", required=True)

    # list-sources
    sub.add_parser("list-sources", help="List connected GitHub repos")

    # create-session
    cs = sub.add_parser("create-session", help="Create a new Jules session")
    cs.add_argument("--prompt", "-p", required=True, help="Task description for Jules")
    cs.add_argument("--source", "-s", help="Source name (e.g. sources/github-owner-repo)")
    cs.add_argument("--branch", default="main", help="Starting branch [default: main]")
    cs.add_argument("--title", "-t", help="Session title")
    cs.add_argument("--require-plan-approval", action="store_true")
    cs.add_argument("--auto-pr", action="store_true", help="Auto-create PR when done")

    # get-session
    gs = sub.add_parser("get-session", help="Get session details")
    gs.add_argument("session_id", help="Session ID")

    # list-sessions
    ls = sub.add_parser("list-sessions", help="List all sessions")
    ls.add_argument("--limit", type=int, default=30)

    # wait
    w = sub.add_parser("wait", help="Wait for a session to complete and print outputs")
    w.add_argument("session_id")
    w.add_argument("--poll-interval", type=int, default=20)
    w.add_argument("--timeout", type=int, default=3600)
    w.add_argument("--no-auto-approve", action="store_true")

    # list-activities
    la = sub.add_parser("list-activities", help="List all activities for a session")
    la.add_argument("session_id")

    # send-message
    sm = sub.add_parser("send-message", help="Send a message to an active session")
    sm.add_argument("session_id")
    sm.add_argument("message")

    # approve-plan
    ap = sub.add_parser("approve-plan", help="Approve a pending plan")
    ap.add_argument("session_id")

    # delete-session
    ds = sub.add_parser("delete-session", help="Delete a session")
    ds.add_argument("session_id")

    args = parser.parse_args()
    _load_dotenv()

    try:
        client = JulesClient(api_key=args.api_key)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    dispatch = {
        "list-sources": _cmd_list_sources,
        "create-session": _cmd_create_session,
        "get-session": _cmd_get_session,
        "list-sessions": _cmd_list_sessions,
        "wait": _cmd_wait,
        "list-activities": _cmd_list_activities,
        "send-message": _cmd_send_message,
        "approve-plan": _cmd_approve_plan,
        "delete-session": _cmd_delete_session,
    }

    try:
        dispatch[args.command](client, args)
    except RuntimeError as e:
        print(f"API error: {e}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError as e:
        print(f"Timeout: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
