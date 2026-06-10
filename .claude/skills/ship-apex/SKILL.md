---
name: ship-apex
description: Full quality-gated ship chain for APEX SWARM — take the current change from working tree to verified production. Chains /simplify → /verify → smoke test → commit → /deploy-apex. Use when the user says "ship it all the way", "ship apex", or "quality pass then deploy". Each stage is a hard gate — stop on first failure and report, never continue downstream.
---

# APEX SWARM — Ship Chain

Run the stages in order. Every stage is a GATE: if it fails, stop immediately, report what failed and why, and do NOT continue to later stages. A chain of non-gating steps just shovels problems downstream faster.

## Stage 1 — Code quality pass
Run the `/simplify` skill on the changed files only (`git diff --name-only` + untracked files that are part of the change). Behavior must not change — smoke test still passes after.

## Stage 2 — Verification
Run the `/verify` skill: confirm the change actually does what it's supposed to by exercising it, not just compiling it. Tests passing ≠ feature working. If the change can't be exercised locally, say so explicitly and ask whether to proceed.

## Stage 3 — Smoke test gate
```bash
python3 -m py_compile main.py && venv/bin/python tests/smoke_test.py 2>&1 | tail -5
```
Must show all snapshot routes present. Any failure: stop, show output.

## Stage 4 — Commit
Commit the change with a clear message (specific files, not `git add -A`). If there are unrelated dirty files, ask before bundling them.

## Stage 5 — Deploy
Run the `/deploy-apex` skill. It has its own gates (compile, smoke test, clean tree, Railway poll, prod route verification) — trust them; do not skip or reorder its steps.

## Stage 6 — Report
One short summary: what shipped, gate results, prod verification status. If any stage was skipped or partially done, say so explicitly.
