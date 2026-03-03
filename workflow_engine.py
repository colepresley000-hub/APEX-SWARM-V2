"""
APEX SWARM - Workflow Engine
=============================
Trigger → Condition → Action automation.

Users define workflows that fire automatically when:
  - A daemon produces an alert
  - An agent completes a task
  - A schedule fires
  - A manual trigger is called via API

Each workflow has:
  - Trigger: what event starts it
  - Conditions: filters (keyword match, agent type, etc.)
  - Actions: what happens (deploy agent, call MCP tool, send Telegram, chain pipeline)

File: workflow_engine.py
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger("apex-swarm")


# ─── WORKFLOW DEFINITION ──────────────────────────────────

class TriggerType:
    DAEMON_ALERT = "daemon.alert"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    SCHEDULE_FIRED = "schedule.fired"
    MANUAL = "manual"
    WEBHOOK = "webhook"


class ActionType:
    DEPLOY_AGENT = "deploy_agent"
    CALL_MCP_TOOL = "call_mcp_tool"
    SEND_TELEGRAM = "send_telegram"
    RUN_PIPELINE = "run_pipeline"
    RUN_COLLABORATION = "run_collaboration"
    WEBHOOK = "webhook"


# ─── WORKFLOW ENGINE ──────────────────────────────────────

class WorkflowEngine:
    """Trigger → Condition → Action automation engine."""

    def __init__(self, db_fn, db_execute_fn, db_fetchall_fn, db_fetchone_fn, user_key_col: str = "user_api_key"):
        self._db_fn = db_fn
        self._db_execute = db_execute_fn
        self._db_fetchall = db_fetchall_fn
        self._db_fetchone = db_fetchone_fn
        self._user_key_col = user_key_col
        self._action_handlers: dict[str, Callable] = {}

    def init_tables(self, conn):
        """Create workflow tables."""
        is_pg = hasattr(conn, 'autocommit')
        if is_pg:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    user_api_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    trigger_type TEXT NOT NULL,
                    trigger_filter TEXT DEFAULT '{}',
                    conditions TEXT DEFAULT '[]',
                    actions TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER DEFAULT 1,
                    fire_count INTEGER DEFAULT 0,
                    last_fired TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_log (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    user_api_key TEXT NOT NULL,
                    trigger_event TEXT DEFAULT '',
                    actions_taken TEXT DEFAULT '[]',
                    success INTEGER DEFAULT 1,
                    error_message TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workflows_user ON workflows(user_api_key);
                CREATE INDEX IF NOT EXISTS idx_workflows_trigger ON workflows(trigger_type);
                CREATE INDEX IF NOT EXISTS idx_workflow_log_wf ON workflow_log(workflow_id);
            """)
            conn.commit()
        else:
            sql = """
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    """ + self._user_key_col + """ TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    trigger_type TEXT NOT NULL,
                    trigger_filter TEXT DEFAULT '{}',
                    conditions TEXT DEFAULT '[]',
                    actions TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER DEFAULT 1,
                    fire_count INTEGER DEFAULT 0,
                    last_fired TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_log (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    """ + self._user_key_col + """ TEXT NOT NULL,
                    trigger_event TEXT DEFAULT '',
                    actions_taken TEXT DEFAULT '[]',
                    success INTEGER DEFAULT 1,
                    error_message TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workflows_trigger ON workflows(trigger_type);
                CREATE INDEX IF NOT EXISTS idx_workflow_log_wf ON workflow_log(workflow_id);
            """
            conn.executescript(sql)
            try:
                conn.execute(f"CREATE INDEX IF NOT EXISTS idx_workflows_user ON workflows({self._user_key_col})")
            except Exception:
                pass
            conn.commit()

    def register_action(self, action_type: str, handler: Callable):
        """Register an action handler function."""
        self._action_handlers[action_type] = handler

    async def create_workflow(
        self,
        user_api_key: str,
        name: str,
        trigger_type: str,
        actions: list[dict],
        description: str = "",
        trigger_filter: dict = None,
        conditions: list[dict] = None,
    ) -> str:
        """Create a new workflow."""
        wf_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        conn = self._db_fn()
        try:
            self._db_execute(conn,
                f"INSERT INTO workflows (id, {self._user_key_col}, name, description, trigger_type, trigger_filter, conditions, actions, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (wf_id, user_api_key, name, description, trigger_type,
                 json.dumps(trigger_filter or {}), json.dumps(conditions or []),
                 json.dumps(actions), now),
            )
            conn.commit()
        finally:
            conn.close()

        return wf_id

    async def process_event(self, event_type: str, event_data: dict, user_api_key: str = None):
        """Process an event — check if any workflows should fire."""
        conn = self._db_fn()
        try:
            if user_api_key:
                rows = self._db_fetchall(conn,
                    f"SELECT id, {self._user_key_col}, name, trigger_filter, conditions, actions FROM workflows WHERE trigger_type = ? AND {self._user_key_col} = ? AND enabled = 1",
                    (event_type, user_api_key),
                )
            else:
                rows = self._db_fetchall(conn,
                    f"SELECT id, {self._user_key_col}, name, trigger_filter, conditions, actions FROM workflows WHERE trigger_type = ? AND enabled = 1",
                    (event_type,),
                )
        finally:
            conn.close()

        if not rows:
            return

        for row in rows:
            wf_id, wf_user, wf_name, filter_json, conds_json, actions_json = row
            try:
                trigger_filter = json.loads(filter_json) if filter_json else {}
                conditions = json.loads(conds_json) if conds_json else []
                actions = json.loads(actions_json) if actions_json else []
            except Exception:
                continue

            # Check trigger filter
            if not self._match_filter(trigger_filter, event_data):
                continue

            # Check conditions
            if not self._check_conditions(conditions, event_data):
                continue

            # Fire the workflow
            logger.info(f"🔥 Workflow fired: {wf_name} ({wf_id[:8]})")
            await self._execute_actions(wf_id, wf_user, wf_name, actions, event_data)

    def _match_filter(self, trigger_filter: dict, event_data: dict) -> bool:
        """Check if event matches the trigger filter."""
        if not trigger_filter:
            return True

        # Agent type filter
        if "agent_type" in trigger_filter:
            if event_data.get("agent_type") != trigger_filter["agent_type"]:
                return False

        # Keyword filter — check if any keyword appears in the message/result
        if "keywords" in trigger_filter:
            text = (event_data.get("message", "") + " " + json.dumps(event_data.get("data", {}))).lower()
            keywords = trigger_filter["keywords"]
            if isinstance(keywords, list):
                if not any(kw.lower() in text for kw in keywords):
                    return False
            elif isinstance(keywords, str):
                if keywords.lower() not in text:
                    return False

        # Namespace filter
        if "namespace" in trigger_filter:
            if event_data.get("namespace") != trigger_filter["namespace"]:
                return False

        return True

    def _check_conditions(self, conditions: list[dict], event_data: dict) -> bool:
        """Evaluate condition list (AND logic)."""
        for cond in conditions:
            op = cond.get("op", "contains")
            field = cond.get("field", "message")
            value = cond.get("value", "")

            actual = str(event_data.get(field, ""))

            if op == "contains" and value.lower() not in actual.lower():
                return False
            elif op == "equals" and actual.lower() != value.lower():
                return False
            elif op == "not_contains" and value.lower() in actual.lower():
                return False
            elif op == "not_empty" and not actual.strip():
                return False

        return True

    async def _execute_actions(self, wf_id: str, user_api_key: str, wf_name: str, actions: list[dict], event_data: dict):
        """Execute all actions for a fired workflow."""
        actions_taken = []
        success = True
        error_msg = ""

        for action in actions:
            action_type = action.get("type", "")
            handler = self._action_handlers.get(action_type)

            if not handler:
                logger.warning(f"No handler for action type: {action_type}")
                actions_taken.append({"type": action_type, "status": "no_handler"})
                continue

            try:
                result = await handler(action, event_data, user_api_key)
                actions_taken.append({"type": action_type, "status": "success", "result": str(result)[:200]})
            except Exception as e:
                logger.error(f"Workflow action failed ({action_type}): {e}")
                actions_taken.append({"type": action_type, "status": "error", "error": str(e)})
                success = False
                error_msg += f"{action_type}: {str(e)}; "

        # Update workflow stats
        conn = self._db_fn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            self._db_execute(conn,
                "UPDATE workflows SET fire_count = fire_count + 1, last_fired = ? WHERE id = ?",
                (now, wf_id),
            )
            # Log execution
            log_id = str(uuid.uuid4())
            self._db_execute(conn,
                f"INSERT INTO workflow_log (id, workflow_id, {self._user_key_col}, trigger_event, actions_taken, success, error_message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (log_id, wf_id, user_api_key, json.dumps(event_data)[:2000], json.dumps(actions_taken), 1 if success else 0, error_msg[:500], now),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_workflows(self, user_api_key: str) -> list[dict]:
        """List all workflows for a user."""
        conn = self._db_fn()
        try:
            rows = self._db_fetchall(conn,
                f"SELECT id, name, description, trigger_type, trigger_filter, actions, enabled, fire_count, last_fired, created_at FROM workflows WHERE {self._user_key_col} = ?",
                (user_api_key,),
            )
        finally:
            conn.close()

        return [
            {
                "workflow_id": r[0], "name": r[1], "description": r[2],
                "trigger_type": r[3], "trigger_filter": json.loads(r[4]) if r[4] else {},
                "actions": json.loads(r[5]) if r[5] else [],
                "enabled": bool(r[6]), "fire_count": r[7],
                "last_fired": r[8], "created_at": r[9],
            }
            for r in rows
        ]

    async def delete_workflow(self, wf_id: str, user_api_key: str) -> bool:
        conn = self._db_fn()
        try:
            self._db_execute(conn,
                f"DELETE FROM workflows WHERE id = ? AND {self._user_key_col} = ?",
                (wf_id, user_api_key),
            )
            conn.commit()
        finally:
            conn.close()
        return True

    async def toggle_workflow(self, wf_id: str, user_api_key: str) -> bool:
        conn = self._db_fn()
        try:
            self._db_execute(conn,
                f"UPDATE workflows SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END WHERE id = ? AND {self._user_key_col} = ?",
                (wf_id, user_api_key),
            )
            conn.commit()
        finally:
            conn.close()
        return True

    async def get_workflow_logs(self, wf_id: str, user_api_key: str, limit: int = 20) -> list[dict]:
        conn = self._db_fn()
        try:
            rows = self._db_fetchall(conn,
                f"SELECT id, trigger_event, actions_taken, success, error_message, created_at FROM workflow_log WHERE workflow_id = ? AND {self._user_key_col} = ? ORDER BY created_at DESC LIMIT ?",
                (wf_id, user_api_key, limit),
            )
        finally:
            conn.close()

        return [
            {
                "log_id": r[0],
                "trigger_event": json.loads(r[1]) if r[1] else {},
                "actions_taken": json.loads(r[2]) if r[2] else [],
                "success": bool(r[3]),
                "error": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]


# ─── PRESET WORKFLOW TEMPLATES ────────────────────────────

WORKFLOW_TEMPLATES = {
    "alert-to-telegram": {
        "name": "Alert → Telegram",
        "description": "Send a Telegram message when any daemon fires an alert",
        "trigger_type": TriggerType.DAEMON_ALERT,
        "trigger_filter": {},
        "actions": [{"type": ActionType.SEND_TELEGRAM, "message": "🚨 Alert: {message}"}],
    },
    "crash-response": {
        "name": "Crash → Deep Analysis",
        "description": "When crypto monitor detects a crash, deploy a full analysis agent",
        "trigger_type": TriggerType.DAEMON_ALERT,
        "trigger_filter": {"keywords": ["crash", "dump", "liquidation", "-10%"]},
        "actions": [
            {"type": ActionType.DEPLOY_AGENT, "agent_type": "macro-analyst", "task": "Analyze the current crypto market crash. What happened, why, and what's the outlook? Event context: {message}"},
            {"type": ActionType.SEND_TELEGRAM, "message": "📊 Crash detected — deploying macro analysis agent"},
        ],
    },
    "yield-alert": {
        "name": "High Yield → Notify",
        "description": "When yield scanner finds high APY opportunity, alert and analyze",
        "trigger_type": TriggerType.DAEMON_ALERT,
        "trigger_filter": {"keywords": ["high yield", "new opportunity", "APY"]},
        "actions": [
            {"type": ActionType.DEPLOY_AGENT, "agent_type": "defi", "task": "Analyze this DeFi yield opportunity for risk and viability: {message}"},
            {"type": ActionType.SEND_TELEGRAM, "message": "💰 Yield opportunity detected — running risk analysis"},
        ],
    },
    "competitor-report": {
        "name": "Competitor Update → Report",
        "description": "When competitor tracker finds something, generate a strategic report",
        "trigger_type": TriggerType.DAEMON_ALERT,
        "trigger_filter": {"keywords": ["launch", "funding", "trending"]},
        "actions": [
            {"type": ActionType.DEPLOY_AGENT, "agent_type": "competitor-analyst", "task": "Analyze this competitive development and its strategic implications: {message}"},
        ],
    },
    "failure-retry": {
        "name": "Agent Failed → Retry",
        "description": "When an agent fails, automatically retry with the same task",
        "trigger_type": TriggerType.AGENT_FAILED,
        "trigger_filter": {},
        "actions": [
            {"type": ActionType.DEPLOY_AGENT, "agent_type": "{agent_type}", "task": "RETRY — Previous attempt failed. {message}"},
        ],
    },
}
