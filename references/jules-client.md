# Jules Client Reference

## jules_client.py CLI

```bash
# List connected GitHub repos
python scripts/jules_client.py list-sources

# Create a session
python scripts/jules_client.py create-session \
  --source "sources/github-owner-repo" \
  --prompt "Fix the bug in auth.py" \
  --branch main \
  --title "Bug fix" \
  --auto-pr                    # push branch + create PR when done

# Get session status
python scripts/jules_client.py get-session SESSION_ID

# Wait for completion (polls until COMPLETED/FAILED, prints final message)
python scripts/jules_client.py wait SESSION_ID \
  --poll-interval 30 \
  --timeout 3600

# List all sessions
python scripts/jules_client.py list-sessions --limit 20

# List all activities for a session (plan steps, messages, bash outputs, patches)
python scripts/jules_client.py list-activities SESSION_ID

# Approve a pending plan (when --require-plan-approval was set)
python scripts/jules_client.py approve-plan SESSION_ID

# Delete a session
python scripts/jules_client.py delete-session SESSION_ID
```

## Using JulesClient as a Python module

```python
from jules_client import JulesClient

client = JulesClient()  # reads JULES_API_KEY from env

# List repos
sources = client.list_sources()

# Create session
session = client.create_session(
    prompt="Your task",
    source="sources/github-owner-repo",
    branch="main",
    title="My session",
    automation_mode="AUTO_CREATE_PR",  # or None
)

# Wait for completion (polls automatically, surfaces questions to terminal)
final = client.wait_for_completion(
    session["id"],
    poll_interval=30,
    timeout=3600,
    auto_approve_plan=True,
)

# Extract outputs
outputs = client.extract_outputs(session["id"])
# outputs["agent_messages"]   — list of Jules' messages
# outputs["bash_outputs"]     — list of {command, output, exitCode}
# outputs["code_changes"]     — list of {source, gitPatch}
# outputs["error"]            — failure reason if any

# Get Jules' latest message (useful to check what it's asking)
question = client.get_latest_agent_message(session["id"])

# Parse autoresearch result JSON from outputs
result = client.extract_result_json(session["id"])
# Searches agent messages first, then bash outputs as fallback
# Returns dict with metric_value, target_met, suggested_params, etc.
```

## Jules CLI (`@google/jules`)

```bash
npm install -g @google/jules
jules login          # OAuth via browser

jules remote list --repo              # list connected repos
jules remote list --session           # list sessions
jules remote new --repo owner/repo --session "task"
jules remote new --repo . --session "task"   # infer repo from cwd
jules remote pull --session SESSION_ID       # pull results/changes
jules                                        # launch TUI dashboard
```

The Jules CLI uses OAuth (unlike the REST API key). It's the only programmatic
way to reply to `AWAITING_USER_FEEDBACK` sessions without going to the web UI.
However, it has no `send-message` subcommand — use the TUI (`jules` with no args)
or the web UI.

## API key limitations

| Operation | API key | OAuth needed |
|---|---|---|
| `GET /sessions` (list) | ✓ | |
| `POST /sessions` (create) | ✓ | |
| `GET /sessions/{id}` | ✓ | |
| `DELETE /sessions/{id}` | ✓ | |
| `GET /sources` | ✓ | |
| `POST .../sendMessage` | ✗ | ✓ |
| `GET .../activities` | ✗ (some sessions) | ✓ |
| `POST .../approvePlan` | ✓ | |
