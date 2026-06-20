# Jules REST API — Quick Reference

**Base URL:** `https://jules.googleapis.com/v1alpha`  
**Auth header:** `x-goog-api-key: $JULES_API_KEY`

---

## Sessions

### Create session
```
POST /sessions
{
  "prompt": "Task for Jules",          // REQUIRED
  "title": "Optional title",
  "sourceContext": {                    // REQUIRED per official schema
    "source": "sources/github-owner-repo",
    "githubRepoContext": { "startingBranch": "main" }
  },
  "requirePlanApproval": false,        // input only — not returned on GET
  "automationMode": "AUTO_CREATE_PR"   // or omit (AUTOMATION_MODE_UNSPECIFIED)
}
→ Returns: Session object (state=QUEUED initially)
```

### Get session
```
GET /sessions/{sessionId}
→ Returns: full Session including outputs[] when COMPLETED
```

### List sessions
```
GET /sessions?pageSize=30            // max 100, default 30
→ Returns: { sessions: [...], nextPageToken: "..." }
```

### Send message (when AWAITING_USER_FEEDBACK)
```
POST /sessions/{sessionId}:sendMessage
{ "prompt": "Your message" }
→ Returns: empty {}
⚠ REQUIRES OAUTH — API keys return 401 on this endpoint.
  Use the Jules web UI: https://jules.google.com/session/{id}
  or the jules CLI TUI (jules login → TUI dashboard)
```

### Approve plan (when AWAITING_PLAN_APPROVAL)
```
POST /sessions/{sessionId}:approvePlan
{}
→ Returns: empty {}
✓ Works with API key
```

### Delete session
```
⚠ Not listed in official Google developers API docs.
  The jules.google.com docs mention it but it may not exist.
  Treat as best-effort / undocumented.
```

---

## Activities

### List activities (auto-paginated)
```
GET /sessions/{sessionId}/activities?pageSize=100
→ Returns: { activities: [...], nextPageToken: "..." }
```
**Pagination:** API caps `pageSize` at 100 (default 50). `list_activities()` in
the Python client follows `nextPageToken` automatically, so you always get all
activities regardless of session length.

### Activity fields
```
{
  "name": "sessions/123/activities/act1",
  "id": "act1",
  "originator": "user|agent|system",
  "description": "...",
  "createTime": "...",
  "artifacts": [...],              // code changes, bash output, media
  "agentMessaged":    { "agentMessage": "..." },
  "userMessaged":     { "userMessage": "..." },
  "planGenerated":    { "plan": { "id": "...", "steps": [...] } },
  "planApproved":     { "planId": "..." },
  "progressUpdated":  { "title": "...", "description": "..." },
  "sessionCompleted": {},
  "sessionFailed":    { "reason": "..." }
}
```

### Artifact types
```json
// Code change
{ "changeSet": {
    "source": "sources/...",
    "gitPatch": {
      "baseCommitId": "a1b2c3",
      "unidiffPatch": "diff --git ...",
      "suggestedCommitMessage": "..."
    }
}}

// Bash command output
{ "bashOutput": {
    "command": "npm test",
    "output": "All tests passed",
    "exitCode": 0
}}

// Media
{ "media": { "mimeType": "image/png", "data": "<base64>" }}
```

---

## Sources

### List sources (connected repos)
```
GET /sources?pageSize=100
→ Returns: { sources: [...] }
```

### Source format
```json
{
  "name": "sources/github-owner-repo",
  "id": "github-owner-repo",
  "githubRepo": {
    "owner": "owner",
    "repo": "repo",
    "isPrivate": false,
    "defaultBranch": { "displayName": "main" },
    "branches": [{ "displayName": "main" }, { "displayName": "dev" }]
  }
}
```

---

## Session States

| State | Meaning |
|-------|---------|
| `QUEUED` | Waiting in queue |
| `PLANNING` | Jules creating a plan |
| `AWAITING_PLAN_APPROVAL` | Waiting for user to approve plan |
| `AWAITING_USER_FEEDBACK` | Jules has a question |
| `IN_PROGRESS` | Jules working |
| `PAUSED` | Paused |
| `COMPLETED` | Done ✓ |
| `FAILED` | Error ✗ |

**Terminal states:** `COMPLETED`, `FAILED`  
**Waiting states (need action):** `AWAITING_PLAN_APPROVAL`, `AWAITING_USER_FEEDBACK`

---

## Session Outputs (after COMPLETED)

```json
{
  "outputs": [
    {
      "pullRequest": {
        "url": "https://github.com/owner/repo/pull/42",
        "title": "...",
        "description": "..."
      }
    }
  ]
}
```

---

## CLI Quick Reference (`@google/jules`)

```bash
npm install -g @google/jules

jules login                                     # Auth via browser
jules logout

jules remote list --repo                        # List connected repos
jules remote list --session                     # List sessions
jules remote new --repo owner/repo --session "task"   # Create session
jules remote new --repo . --session "task"      # Use current dir's repo
jules remote new --repo . --parallel 5 --session "task"  # 5 parallel sessions
jules remote pull --session SESSION_ID          # Pull results/changes

jules                                           # Launch TUI dashboard
```

---

## Python Client Quick Reference

```python
from jules_client import JulesClient

client = JulesClient()  # uses JULES_API_KEY env var

# List repos
client.list_sources()

# Create session
session = client.create_session(
    prompt="Your task",
    source="sources/github-owner-repo",
    branch="main",
    title="My session",
    require_plan_approval=False,
    automation_mode=None,  # or "AUTO_CREATE_PR"
)

# Wait for completion (polls automatically)
final = client.wait_for_completion(
    session["id"],
    poll_interval=20,
    timeout=3600,
    auto_approve_plan=True,
)

# Extract outputs
outputs = client.extract_outputs(session["id"])
# outputs["agent_messages"]   — list of Jules messages
# outputs["bash_outputs"]     — list of {command, output, exitCode}
# outputs["code_changes"]     — list of {source, gitPatch}
# outputs["error"]            — failure reason if any

# Parse autoresearch result JSON from Jules' final message
result = client.extract_result_json(session["id"])
# result["metric_value"], result["target_met"], result["suggested_keywords"]
```

## API Key Limitations (confirmed by live testing)

| Endpoint | API Key | Notes |
|----------|---------|-------|
| `sessions.create` | ✓ | Works |
| `sessions.get` | ✓ | Works |
| `sessions.list` | ✓ | Works |
| `sessions.approvePlan` | ✓ | Works |
| `sessions.sendMessage` | ✗ | **Requires OAuth** — returns 401 |
| `sessions.activities.list` | ✓ | Works |
| `sessions.activities.get` | ✓ | Works |
| `sources.list` | ✓ | Works |
| `sources.get` | ✓ | Works |
| `sessions.delete` | ? | Not in official docs, may not exist |

When Jules enters `AWAITING_USER_FEEDBACK`, you must respond via:
- **Web UI**: `https://jules.google.com/session/{id}` 
- **Jules CLI TUI**: run `jules` (requires `jules login` OAuth)

The Python client's `wait_for_completion()` surfaces Jules' question in full
and keeps polling until you reply. It does not attempt to auto-respond.

---



- **15 parallel sessions** active at once
- **100 sessions/day** total
- No apparent token limits per session

---

## Error Codes

| Code | Meaning |
|------|---------|
| 400 | Bad request (invalid params) |
| 401 | Invalid/missing API key |
| 403 | Insufficient permissions |
| 404 | Resource not found |
| 429 | Rate limited |
| 500 | Server error |

Error body: `{ "error": { "code": 400, "message": "...", "status": "INVALID_ARGUMENT" } }`
