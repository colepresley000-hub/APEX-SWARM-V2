---
name: deploy-apex
description: Full verified deploy of APEX SWARM to production (swarmsfall.com). Runs compile check → smoke test → clean-export upload → polls Railway until SUCCESS → verifies prod routes. Use whenever the user says "deploy", "ship it", "railway up", or "push to prod". NEVER deploy if smoke test fails — stop and report instead.
---

# APEX SWARM — Verified Production Deploy

Run every step in order. Stop immediately if any gate fails — report what failed, do NOT continue to Railway.

## Step 1 — Syntax gate
```bash
python3 -m py_compile main.py
```
Must exit 0. If it fails: show the error, stop, do not proceed.

## Step 2 — Route count gate
```bash
venv/bin/python tests/smoke_test.py 2>&1 | tail -5
```
Must show `✅ all 161 snapshot routes still present`. If count ≠ 161 or the line is missing: stop, show the output, do not proceed.

## Step 3 — Working tree must be clean
```bash
git status --short
```
Any uncommitted changes should be committed first. If there are staged/unstaged changes, tell the user and ask whether to commit them before deploying. Untracked files (cole_persona.py, fix_*.py) are fine — they go into the export but don't affect routes.

## Step 4 — Clean export (avoids the sandbox-socket tar issue)
```bash
rm -rf /tmp/apex-deploy && mkdir -p /tmp/apex-deploy
git archive --format=tar HEAD | tar -x -C /tmp/apex-deploy
cp railway.json cole_persona.py /tmp/apex-deploy/ 2>/dev/null
ls /tmp/apex-deploy | head -5   # sanity check
```

## Step 5 — Link temp dir to Railway project (dangerouslyDisableSandbox: true)
```bash
cd /tmp/apex-deploy && railway link \
  --project 98a99216-8f59-4d54-bcd2-e55ea0471786 \
  --environment production \
  --service ca13292e-850e-40a2-9049-cc81e64ed56c 2>&1 | tail -3
```

## Step 6 — Upload (dangerouslyDisableSandbox: true)
```bash
cd /tmp/apex-deploy && railway up --detach 2>&1; echo "EXIT=$?"
```
Must show `Uploading...` and a Build Logs URL. If EXIT=1 or no "Uploading..." line appears, something is wrong — report it and stop.

## Step 7 — Poll until terminal state (dangerouslyDisableSandbox: true)
```bash
# capture the new deployment ID from the first line of the list
while true; do
  line=$(railway deployment list 2>/dev/null | grep -v "Recent Deployments" | head -1)
  echo "$line" | grep -qiE "SUCCESS|FAILED|CRASHED" && { echo "$line"; break; }
  echo "still building... $line"
  sleep 12
done
```
If FAILED or CRASHED: fetch logs (`railway logs --lines 50`) and report. Do NOT retry automatically.

## Step 8 — Live verification
```bash
echo "telegram/status: $(curl -s -o /dev/null -w '%{http_code}' https://swarmsfall.com/api/v1/telegram/status)"
echo "byok/status:     $(curl -s -o /dev/null -w '%{http_code}' https://swarmsfall.com/api/v1/byok/status)"
echo "health:          $(curl -s -o /dev/null -w '%{http_code}' https://swarmsfall.com/api/v1/health)"
```
Expected: `401 / 401 / 200`. If health ≠ 200, the app didn't start — pull logs immediately.

## Reporting
On success, tell the user:
- ✅ deployed (commit hash or deployment ID)
- which routes verified green
- anything worth noting (e.g. untracked files included)

On failure, tell the user exactly which step failed, what the output was, and what to do next. Never summarise a failure as "something went wrong."
