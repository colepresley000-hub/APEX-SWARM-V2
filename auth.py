"""
APEX SWARM — Authentication & audit helpers.

Password hashing (PBKDF2), session-token verification, org-member lookup, and
the audit-log writer. All pure functions over the db layer — no module-level
state, so this is a clean extraction from main.py.

Note: make_token signs with JWT_SECRET via HMAC-SHA256. Despite the name,
these are opaque signed tokens, not RFC-7519 JWTs.
"""
import base64 as _b64
import hashlib as _hl
import hmac as _hm
import json as _json
import logging
import os
import uuid
from datetime import datetime, timezone

from config import JWT_SECRET
from db import db_execute, db_fetchone, get_db

logger = logging.getLogger("apex-swarm")


def hash_password(pw):
    salt = os.urandom(16)
    dk = _hl.pbkdf2_hmac("sha256", pw.encode(), salt, 100000)
    return _b64.b64encode(salt + dk).decode()


def verify_password(pw, stored):
    try:
        raw = _b64.b64decode(stored.encode())
        salt, dk = raw[:16], raw[16:]
        check = _hl.pbkdf2_hmac("sha256", pw.encode(), salt, 100000)
        return _hm.compare_digest(dk, check)
    except Exception:
        return False


def make_token(user_id):
    p = _json.dumps({"u": user_id, "t": datetime.now(timezone.utc).isoformat()})
    s = _hm.new(JWT_SECRET.encode(), p.encode(), _hl.sha256).hexdigest()
    return _b64.urlsafe_b64encode((p + "|" + s).encode()).decode()


def get_user_by_token(token):
    try:
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        row = db_fetchone(conn,
            "SELECT s.user_id,u.email,u.tier,u.api_key,u.org_id,u.role FROM sessions s "
            "JOIN users u ON s.user_id=u.id WHERE s.token=? AND s.expires_at>?",
            (token, now))
        conn.close()
        if row:
            return {"user_id":row[0],"email":row[1],"tier":row[2],"api_key":row[3],"org_id":row[4],"role":row[5]}
        return None
    except Exception:
        return None


def get_member_by_key(api_key):
    conn = get_db()
    try:
        row = db_fetchone(conn,
            "SELECT m.id,m.org_id,m.email,m.role,o.name,o.tier,o.slack_webhook,o.slack_channel "
            "FROM org_members m JOIN orgs o ON m.org_id=o.id WHERE m.api_key=?",
            (api_key,))
        if row:
            return {"member_id":row[0],"org_id":row[1],"email":row[2],"role":row[3],
                    "org_name":row[4],"tier":row[5],"slack_webhook":row[6],"slack_channel":row[7]}
        return None
    finally:
        conn.close()


def log_audit(user_email, action, resource="", detail="", user_id="", org_id="", ip=""):
    try:
        conn = get_db()
        db_execute(conn, "INSERT INTO audit_log (id,user_id,user_email,org_id,action,resource,detail,ip,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()),user_id,user_email,org_id,action,resource,str(detail)[:500],ip,datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Audit: " + str(e))
