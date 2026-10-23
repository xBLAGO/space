"""Explicit, additive Identity Store migration. Never invoked by application startup.

Run only against an initialized Foundation database copy after an online backup.
No account is promoted to verified and no legacy data is copied by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


VERSION = "foundation05_identity_001"
DDL = (
    """CREATE TABLE identity_schema_migrations (
        version TEXT PRIMARY KEY NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL
    )""",
    """CREATE TABLE user_devices (
        device_id TEXT PRIMARY KEY NOT NULL,
        blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
        label TEXT CHECK(label IS NULL OR length(label) <= 80),
        created_at INTEGER NOT NULL CHECK(created_at >= 0),
        revoked_at INTEGER CHECK(revoked_at IS NULL OR revoked_at >= created_at),
        UNIQUE(blago_user_id, device_id)
    )""",
    """CREATE TABLE passkey_credentials (
        credential_id BLOB PRIMARY KEY NOT NULL CHECK(typeof(credential_id) = 'blob' AND length(credential_id) BETWEEN 1 AND 1024),
        blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
        device_id TEXT,
        public_key_cose BLOB NOT NULL CHECK(typeof(public_key_cose) = 'blob' AND length(public_key_cose) BETWEEN 1 AND 4096),
        user_handle BLOB NOT NULL CHECK(typeof(user_handle) = 'blob' AND length(user_handle) BETWEEN 1 AND 64),
        sign_count INTEGER NOT NULL DEFAULT 0 CHECK(sign_count >= 0),
        backup_eligible INTEGER NOT NULL CHECK(backup_eligible IN (0,1)),
        backup_state INTEGER NOT NULL CHECK(backup_state IN (0,1) AND backup_state <= backup_eligible),
        created_at INTEGER NOT NULL CHECK(created_at >= 0),
        revoked_at INTEGER CHECK(revoked_at IS NULL OR revoked_at >= created_at),
        FOREIGN KEY(blago_user_id, device_id) REFERENCES user_devices(blago_user_id, device_id)
    )""",
    """CREATE TABLE auth_challenges (
        challenge_hash TEXT PRIMARY KEY NOT NULL CHECK(length(challenge_hash) = 64 AND challenge_hash NOT GLOB '*[^0-9a-f]*'),
        blago_user_id TEXT REFERENCES users(blago_user_id),
        purpose TEXT NOT NULL CHECK(purpose IN ('register_passkey','authenticate','link_account')),
        binding_hash TEXT NOT NULL CHECK(length(binding_hash) = 64 AND binding_hash NOT GLOB '*[^0-9a-f]*'),
        rp_id TEXT NOT NULL CHECK(length(rp_id) BETWEEN 1 AND 253),
        origin TEXT NOT NULL CHECK(length(origin) BETWEEN 1 AND 2048),
        created_at INTEGER NOT NULL CHECK(created_at >= 0),
        expires_at INTEGER NOT NULL CHECK(expires_at > created_at AND expires_at <= created_at + 600),
        consumed_at INTEGER CHECK(consumed_at IS NULL OR consumed_at BETWEEN created_at AND expires_at),
        CHECK(purpose = 'authenticate' OR blago_user_id IS NOT NULL)
    )""",
    """CREATE TABLE activity_events (
        event_id TEXT PRIMARY KEY NOT NULL,
        blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
        event_type TEXT NOT NULL CHECK(event_type IN ('login','logout','link','unlink','consent','passkey_added','passkey_revoked')),
        channel TEXT NOT NULL CHECK(channel IN ('web','telegram','mini_app')),
        occurred_at INTEGER NOT NULL CHECK(occurred_at >= 0),
        expires_at INTEGER NOT NULL CHECK(expires_at > occurred_at)
    )""",
    """CREATE TABLE security_signals (
        signal_id TEXT PRIMARY KEY NOT NULL,
        blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
        signal_type TEXT NOT NULL CHECK(signal_type IN ('replay','counter_anomaly','failed_auth','new_credential')),
        resolution TEXT NOT NULL DEFAULT 'pending' CHECK(resolution IN ('pending','dismissed','reauthenticated')),
        occurred_at INTEGER NOT NULL CHECK(occurred_at >= 0),
        expires_at INTEGER NOT NULL CHECK(expires_at > occurred_at)
    )""",
    """CREATE TABLE consent_records (
        consent_id TEXT PRIMARY KEY NOT NULL,
        blago_user_id TEXT NOT NULL REFERENCES users(blago_user_id),
        purpose TEXT NOT NULL CHECK(purpose IN ('optional_activity','device_label')),
        policy_version TEXT NOT NULL CHECK(length(policy_version) BETWEEN 1 AND 80),
        decision TEXT NOT NULL CHECK(decision IN ('granted','withdrawn')),
        recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0)
    )""",
    "CREATE INDEX idx_passkey_user ON passkey_credentials(blago_user_id)",
    "CREATE INDEX idx_challenge_expiry ON auth_challenges(expires_at)",
    "CREATE INDEX idx_activity_user_time ON activity_events(blago_user_id, occurred_at)",
    "CREATE INDEX idx_security_user_time ON security_signals(blago_user_id, occurred_at)",
    "CREATE INDEX idx_consent_user_time ON consent_records(blago_user_id, recorded_at)",
)
CHECKSUM = hashlib.sha256("\n".join(DDL).encode()).hexdigest()


def _objects(connection: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    return {row[0]: (row[1], row[2]) for row in connection.execute(
        "SELECT name, type, sql FROM sqlite_master WHERE sql IS NOT NULL"
    )}


def _expected_objects() -> dict[str, tuple[str, str]]:
    with sqlite3.connect(":memory:") as reference:
        for statement in DDL:
            reference.execute(statement)
        return _objects(reference)


def _check_database(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("SQLite foreign_key_check failed")


def migrate_identity_store(path: Path) -> bool:
    """Return True if applied; reject missing foundations, collisions and drift.

    Requires existing users table from SQLiteBlagoStore. A completely blank file
    is rejected without changes: foundation bootstrapping is a separate operation.
    Existing connections must be quiesced for offline operation and backup.
    """
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        _check_database(connection)
        objects = _objects(connection)
        columns = connection.execute("PRAGMA table_info(users)").fetchall()
        if not any(row[1] == "blago_user_id" and row[2].upper() == "TEXT" and row[5] == 1 for row in columns):
            raise ValueError("Initialize Foundation SQLiteBlagoStore first")
        expected = _expected_objects()
        present = set(expected) & set(objects)
        if present:
            if any(objects.get(name) != value for name, value in expected.items()):
                raise ValueError("Identity schema collision or drift")
            rows = connection.execute("SELECT version, checksum FROM identity_schema_migrations").fetchall()
            if rows != [(VERSION, CHECKSUM)]:
                raise ValueError("Unexpected identity migration ledger")
            connection.commit()
            return False
        for statement in DDL:
            connection.execute(statement)  # executescript would implicitly commit!
        connection.execute("INSERT INTO identity_schema_migrations VALUES (?, ?, ?)",
                           (VERSION, CHECKSUM, datetime.now(timezone.utc).isoformat()))
        _check_database(connection)
        connection.commit()
        return True
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Existing offline SQLite COPY; never a production path")
    args = parser.parse_args()
    print("applied" if migrate_identity_store(args.database) else "already applied")


if __name__ == "__main__":
    main()
