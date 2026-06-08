---
name: sprint1-loop
description: Autonomous Sprint-1 module decomposition loop for APEX SWARM. Extracts route groups from main.py into routes/*.py one at a time, smoke-tests after every extraction, commits on pass, loops until no more safe extractions remain. Use when the user says "continue the refactor", "keep extracting", "sprint1 loop", or "keep going on main.py". NEVER deploys — reports when done and asks.
---

# APEX SWARM — Sprint-1 Extraction Loop

## What this is
main.py is a ~7900-line FastAPI god-file being decomposed into focused modules.
Leaf modules (config, db, auth, byok, telegram, slack, deps) are already extracted.
Two APIRouter modules (routes/telegram.py, routes/byok.py) are already live.
This skill extracts the next safe route groups — one at a time — and runs the smoke test as a hard gate between each.

## The loop protocol

Repeat until no more safe candidates remain:

1. **Pick the next candidate** — see "Extraction candidates" below. Start from the top of the list.
2. **Read every route handler** in that group inside main.py.
3. **Run the safety audit** — see "Safety rules" below. If the group fails the audit, mark it DEFERRED and move to the next candidate.
4. **Extract** — create `routes/<group>.py` as an `APIRouter`. Copy only the route handlers (and any helper functions used exclusively by this group). Follow the import layering rules.
5. **Stub out in main.py** — replace the extracted block with:
   ```python
   # Extracted to routes/<group>.py
   from routes.<group> import router as <group>_router
   app.include_router(<group>_router)
   ```
   Do this in the same position so Starlette's registration order is preserved.
6. **Smoke test gate**:
   ```bash
   python3 -m py_compile main.py && venv/bin/python tests/smoke_test.py 2>&1 | tail -3
   ```
   - PASS (`✅ all 161 snapshot routes`): commit and continue.
   - FAIL: **revert immediately** (`git checkout main.py routes/<group>.py`), mark the group DEFERRED with the failure reason, move to the next candidate.
7. **Commit**:
   ```bash
   git add main.py routes/<group>.py
   git commit -m "refactor: extract /api/v1/<group> routes into routes/<group>.py"
   ```
8. Loop back to step 1.

When the loop ends: push all commits (`git push origin main`) and report a summary — what was extracted, what was deferred and why. Then ask the user: "Want me to deploy?"

**NEVER deploy automatically.** Use `/deploy-apex` if the user says yes.

---

## Import layering rules (MUST follow — no exceptions)

```
config.py            (leaf — no deps)
  └─ db.py
       ├─ auth.py    (config + db)
       ├─ byok.py    (config + db)
       ├─ telegram.py(config + db)
       └─ slack.py   (config + db)
deps.py              (fastapi only)

routes/<group>.py    (may import: config, db, auth, byok, telegram, slack, deps)
                     (NEVER import main — would be circular)

main.py              (imports everything; registers routers)
```

Any function a new route module needs that currently lives in main.py **must** be moved to an appropriate leaf module first (or a new leaf module created), never imported from main.

---

## Safety audit — check each candidate route group for these blockers

A route group is **UNSAFE** (defer it) if any handler references:
- `daemon_manager` — set by init_db(), lives in main namespace
- `identity_manager` — same
- `swarm_memory` — same
- `mcp_registry` — same
- `tier_enforcer` — same
- `workflow_engine` — same
- `marketplace` — same
- `a2a_engine` — same
- `goal_engine` — same
- `conversation_store` — same
- `audit_log` — same
- `execute_task()` — 970-line core loop, explicitly deferred
- `active_daemons` — mutable runtime dict in main namespace
- `_daemon_execute_fn` — closure over execute_task
- `event_bus` — mission control runtime object set in init_db

**Special case — `USER_KEY_COL`:** This is set by init_db() but its value is always the string `"user_api_key"`. If a candidate route group only uses `USER_KEY_COL` (not any of the above), it is SAFE to extract provided you first add this to `db.py`:
```python
USER_KEY_COL = "user_api_key"   # column name in usage_log / agents tables
```
…and import it from there in the new route module. Do this once (check if it already exists in db.py before adding). Then the group is no longer blocked.

---

## Extraction candidates (priority order)

Work through this list top-to-bottom. Each item shows the route prefix(es) and the likely blockers to check:

1. **auth** — `/api/v1/auth/*` (signup, login, logout, me, google, google/callback)
   - Expected deps: `auth.py` functions, `get_db`, `get_api_key`. Likely safe.
   - Watch for: JWT/token handling that may call functions still in main.py.

2. **usage** — `/api/v1/usage`, `/api/v1/usage/summary`, `/api/v1/usage/leaderboard`
   - Expected deps: db only, USER_KEY_COL (handle per special case above), TIER_LIMITS dict.
   - Watch for: TIER_LIMITS — if it's a module-level constant in main.py (not set by init_db), it can be moved to config.py first.

3. **billing** — `/api/v1/billing/*` (checkout, status, webhook)
   - Expected deps: Stripe env vars (in config.py), db. Likely safe.
   - Watch for: any reference to tier_enforcer.

4. **health** — `/api/v1/health`
   - Reads many module-level flags (VERSION, AGENTS, TOOLS_AVAILABLE, etc.). These are compile-time constants, not init_db globals, so extracting is technically safe — but the handler is short and the flags list is long. Judgment call: skip unless health is causing problems.

5. **knowledge** — `/api/v1/knowledge`
   - Check for swarm_memory dependency. If present: defer.

6. **models** — `/api/v1/models`, `/api/v1/models/available`
   - Likely reads a static list or multi_model module. Check for globals.

7. **history** — `/api/v1/history`
   - Reads from `agents` table in db. Should be safe. Check for USER_KEY_COL (handle per special case).

8. **license** — `/api/v1/license/validate`
   - Calls `get_or_validate_license`. If that function is defined in main.py, move it to a new leaf module `license.py` first.

9. **discord** — `/api/v1/discord/webhook`
   - Check for channels/command_router globals.

10. **skills** — `/api/v1/skills`
    - Likely reads slash_skills.py output. Check if slash_skills is already importable as a leaf.

**Explicitly deferred — do NOT attempt:**
- `/api/v1/daemons/*` — daemon_manager
- `/api/v1/identity/*` — identity_manager
- `/api/v1/goals/*` — goal_engine
- `/api/v1/workflows/*` — workflow_engine
- `/api/v1/marketplace/*` — marketplace
- `/api/v1/a2a/*` — a2a_engine
- `/api/v1/memory/*` — swarm_memory
- `/api/v1/mcp/*` — mcp_registry
- `/api/v1/metrics/*` — tier_enforcer / daemon_manager
- `/api/v1/deploy` — execute_task
- `/api/v1/voice/*` — check, likely safe but low value vs risk

---

## Key constraints

- **One group per commit.** Never batch two extractions into one commit — makes rollback surgical.
- **Smoke test is the gate.** A passing compile is not enough. The route count must stay at 161.
- **Preserve registration order.** Starlette matches the FIRST registered route. When you replace a block with `app.include_router(...)`, put it at the same relative position.
- **No behaviour changes.** If a handler has a bug, leave the bug. Fix bugs in separate commits.
- **Stop and report** if anything unexpected happens (import errors, test failures after revert, etc.). Do not try to fix things silently.
