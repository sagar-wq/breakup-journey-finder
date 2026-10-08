"""
Access-code login system.

This is NOT a user-account system - there are no usernames or profiles.
It's a single shared front door: the admin generates 10-character
alphanumeric codes from /admin, hands them out to whoever should have
access, and anyone holding a non-revoked code can log in at /login. A code
is reusable (the same person can log in with it repeatedly) until the admin
revokes it.

Codes are stored as SHA-256 hashes, never in plaintext, so a leaked copy of
the database file doesn't hand out working codes. The plaintext is shown
to the admin exactly once, at generation time, in the admin panel - there
is no way to recover it afterwards (the same as a password reset flow:
generate a new code and revoke the old one).

Storage is a single local SQLite file. On a host with ephemeral disk (e.g.
Render's free plan wipes local disk on every redeploy - not on every
sleep/wake, just on redeploys), that means codes issued before a redeploy
stop working after one and need re-generating. Fine for a small invite-only
tool; swap for a real database if that becomes a problem.
"""

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "access_codes.db")

# Excludes 0/O and 1/I/L - characters people misread or mistype when a code
# is read aloud or copied from a screenshot.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 10


def _hash_code(code):
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS access_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT UNIQUE NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            use_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def generate_code(label=""):
    """Create a new code and return its PLAINTEXT - the only time it's ever
    available. Retries on the astronomically unlikely chance of a hash
    collision with an existing code."""
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO access_codes (code_hash, label, created_at) VALUES (?, ?, ?)",
                (_hash_code(code), label.strip()[:100], datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return code
        except sqlite3.IntegrityError:
            continue  # hash collision - vanishingly rare, just try another code
        finally:
            conn.close()


def validate_code(code):
    """Return True and record usage if `code` matches a non-revoked code."""
    if not code:
        return False
    conn = _connect()
    row = conn.execute(
        "SELECT id FROM access_codes WHERE code_hash = ? AND revoked = 0",
        (_hash_code(code),),
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE access_codes SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), row["id"]),
        )
        conn.commit()
    conn.close()
    return row is not None


def list_codes():
    conn = _connect()
    rows = conn.execute(
        "SELECT id, label, created_at, revoked, last_used_at, use_count "
        "FROM access_codes ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def revoke_code(code_id):
    conn = _connect()
    conn.execute("UPDATE access_codes SET revoked = 1 WHERE id = ?", (code_id,))
    conn.commit()
    conn.close()


def unrevoke_code(code_id):
    conn = _connect()
    conn.execute("UPDATE access_codes SET revoked = 0 WHERE id = ?", (code_id,))
    conn.commit()
    conn.close()
