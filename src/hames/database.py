"""SQLite connection and migration infrastructure."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class MigrationError(RuntimeError):
    """A durable schema migration could not be applied safely."""


def _sha256_sql(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Migration:
    id: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


MIGRATIONS = (
    Migration(
        1,
        "application metadata",
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
    Migration(
        2,
        "core event ledger",
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            closed_at TEXT,
            status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'cancelled', 'failed')),
            title TEXT,
            working_directory TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            reasoning_effort TEXT NOT NULL
        );

        CREATE TABLE events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            run_id TEXT,
            agent_id TEXT,
            type TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            causation_id TEXT,
            correlation_id TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX events_session_sequence_idx ON events(session_id, sequence);
        CREATE INDEX events_run_idx ON events(run_id) WHERE run_id IS NOT NULL;

        CREATE TRIGGER events_no_update
        BEFORE UPDATE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are append-only');
        END;

        CREATE TRIGGER events_no_delete
        BEFORE DELETE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are append-only');
        END;
        """,
    ),
    Migration(
        3,
        "branching and payload integrity",
        """
        DROP TRIGGER events_no_update;
        DROP TRIGGER events_no_delete;
        DROP INDEX events_session_sequence_idx;
        DROP INDEX events_run_idx;

        ALTER TABLE events RENAME TO events_m00;

        CREATE TABLE events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            run_id TEXT,
            agent_id TEXT,
            type TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            causation_id TEXT,
            correlation_id TEXT,
            payload_json TEXT,
            blob_hash TEXT,
            payload_hash TEXT NOT NULL,
            redaction_state TEXT NOT NULL CHECK (redaction_state IN ('none', 'redacted')),
            CHECK ((payload_json IS NOT NULL) != (blob_hash IS NOT NULL))
        );

        INSERT INTO events(
            sequence, id, session_id, run_id, agent_id, type, schema_version,
            created_at, causation_id, correlation_id, payload_json, blob_hash,
            payload_hash, redaction_state
        )
        SELECT
            sequence, id, session_id, run_id, agent_id, type, schema_version,
            created_at, causation_id, correlation_id, payload_json, NULL,
            sha256(payload_json), 'none'
        FROM events_m00;

        DROP TABLE events_m00;

        CREATE INDEX events_session_sequence_idx ON events(session_id, sequence);
        CREATE INDEX events_run_idx ON events(run_id) WHERE run_id IS NOT NULL;

        CREATE TRIGGER events_no_update
        BEFORE UPDATE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are append-only');
        END;

        CREATE TRIGGER events_no_delete
        BEFORE DELETE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are append-only');
        END;

        ALTER TABLE sessions
            ADD COLUMN parent_session_id TEXT REFERENCES sessions(id);
        ALTER TABLE sessions
            ADD COLUMN fork_event_id TEXT REFERENCES events(id);
        CREATE INDEX sessions_parent_idx ON sessions(parent_session_id);
        """,
    ),
    Migration(
        4,
        "trusted roots and approvals",
        """
        CREATE TABLE trusted_roots (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE approvals (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            run_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            working_directory TEXT NOT NULL,
            tool_call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'approved', 'denied', 'cancelled')
            ),
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE INDEX approvals_run_idx ON approvals(run_id);
        CREATE INDEX approvals_pending_idx ON approvals(status) WHERE status = 'pending';
        """,
    ),
    Migration(
        5,
        "model context capacity",
        """
        ALTER TABLE sessions
            ADD COLUMN context_window_tokens INTEGER NOT NULL DEFAULT 32768;
        ALTER TABLE sessions
            ADD COLUMN context_window_source TEXT NOT NULL DEFAULT 'fallback'
            CHECK (context_window_source IN ('profile', 'provider', 'fallback'));
        """,
    ),
    Migration(
        6,
        "agent lineage",
        """
        ALTER TABLE sessions
            ADD COLUMN lineage_kind TEXT NOT NULL DEFAULT 'root'
            CHECK (lineage_kind IN ('root', 'branch', 'delegation'));
        ALTER TABLE sessions
            ADD COLUMN delegation_depth INTEGER NOT NULL DEFAULT 0;
        UPDATE sessions SET lineage_kind = 'branch' WHERE parent_session_id IS NOT NULL;
        """,
    ),
    Migration(
        7,
        "layered memory",
        """
        CREATE TABLE memory_records (
            id TEXT PRIMARY KEY,
            layer TEXT NOT NULL CHECK (layer IN ('relationship', 'semantic', 'episodic')),
            status TEXT NOT NULL CHECK (
                status IN ('proposed', 'active', 'rejected', 'superseded', 'retracted')
            ),
            visibility TEXT NOT NULL CHECK (
                visibility IN ('global', 'agent_private', 'workspace', 'session_team')
            ),
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            value_json TEXT NOT NULL,
            summary TEXT NOT NULL,
            confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            importance REAL NOT NULL CHECK (importance >= 0 AND importance <= 1),
            owner_agent_id TEXT,
            workspace_path TEXT,
            lineage_root_session_id TEXT REFERENCES sessions(id),
            source_session_id TEXT NOT NULL REFERENCES sessions(id),
            source_run_id TEXT,
            origin_kind TEXT NOT NULL CHECK (
                origin_kind IN ('automatic', 'explicit', 'episode')
            ),
            valid_from TEXT,
            valid_until TEXT,
            superseded_by_id TEXT REFERENCES memory_records(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (visibility != 'agent_private' OR owner_agent_id IS NOT NULL),
            CHECK (visibility != 'workspace' OR workspace_path IS NOT NULL),
            CHECK (visibility != 'session_team' OR lineage_root_session_id IS NOT NULL)
        );

        CREATE TABLE memory_anchors (
            memory_id TEXT NOT NULL REFERENCES memory_records(id),
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (memory_id, kind, value)
        );

        CREATE TABLE memory_provenance (
            memory_id TEXT NOT NULL REFERENCES memory_records(id),
            event_id TEXT NOT NULL REFERENCES events(id),
            PRIMARY KEY (memory_id, event_id)
        );

        CREATE VIRTUAL TABLE memory_fts USING fts5(
            memory_id UNINDEXED,
            subject,
            predicate,
            summary,
            value
        );

        CREATE TABLE memory_jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('extraction', 'explicit_capture')),
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'running', 'completed', 'failed', 'cancelled')
            ),
            session_id TEXT NOT NULL REFERENCES sessions(id),
            run_id TEXT,
            source_event_id TEXT NOT NULL REFERENCES events(id),
            content TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (kind, session_id, run_id, source_event_id)
        );

        CREATE INDEX memory_status_layer_idx ON memory_records(status, layer);
        CREATE INDEX memory_workspace_idx ON memory_records(workspace_path);
        CREATE INDEX memory_agent_idx ON memory_records(owner_agent_id);
        CREATE INDEX memory_lineage_idx ON memory_records(lineage_root_session_id);
        CREATE UNIQUE INDEX memory_episode_run_idx ON memory_records(source_run_id)
            WHERE origin_kind = 'episode';
        CREATE INDEX memory_jobs_status_idx ON memory_jobs(status, created_at);

        CREATE TRIGGER memory_records_no_delete
        BEFORE DELETE ON memory_records
        BEGIN
            SELECT RAISE(ABORT, 'memory records cannot be deleted');
        END;
        """,
    ),
    Migration(
        8,
        "autonomous skills",
        """
        CREATE TABLE skills (
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL,
            scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace', 'agent')),
            scope_key TEXT,
            active_version_id TEXT,
            pinned_version_id TEXT,
            archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (slug, scope, scope_key),
            CHECK (scope = 'global' OR scope_key IS NOT NULL)
        );

        CREATE TABLE skill_versions (
            id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL REFERENCES skills(id),
            version INTEGER NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK (
                status IN ('draft', 'verified', 'active', 'stale', 'archived',
                           'rejected', 'quarantined', 'superseded')
            ),
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            instructions TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            package_path TEXT NOT NULL,
            base_version_id TEXT REFERENCES skill_versions(id),
            created_by TEXT NOT NULL CHECK (created_by IN ('automatic', 'agent', 'user', 'import')),
            source_session_id TEXT NOT NULL REFERENCES sessions(id),
            source_run_id TEXT,
            created_at TEXT NOT NULL,
            activated_at TEXT,
            last_used_at TEXT,
            UNIQUE (skill_id, version)
        );

        CREATE TABLE skill_evidence (
            version_id TEXT NOT NULL REFERENCES skill_versions(id),
            event_id TEXT NOT NULL REFERENCES events(id),
            PRIMARY KEY (version_id, event_id)
        );

        CREATE TABLE skill_evaluations (
            id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES skill_versions(id),
            kind TEXT NOT NULL CHECK (kind IN ('deterministic', 'model', 'script')),
            status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
            score REAL NOT NULL CHECK (score >= 0 AND score <= 1),
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE skill_jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('author', 'patch', 'revalidate')),
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'budget_wait')
            ),
            session_id TEXT NOT NULL REFERENCES sessions(id),
            run_id TEXT,
            source_event_id TEXT NOT NULL REFERENCES events(id),
            target_skill_id TEXT REFERENCES skills(id),
            goal TEXT NOT NULL,
            scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace', 'agent')),
            attempts INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (kind, session_id, run_id, source_event_id)
        );

        CREATE TABLE workflow_signatures (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            agent_id TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            task_text TEXT NOT NULL,
            task_tokens_json TEXT NOT NULL,
            tool_sequence_json TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('completed', 'failed', 'cancelled')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE skill_usage (
            version_id TEXT NOT NULL REFERENCES skill_versions(id),
            run_id TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            catalogued INTEGER NOT NULL DEFAULT 0 CHECK (catalogued IN (0, 1)),
            loaded INTEGER NOT NULL DEFAULT 0 CHECK (loaded IN (0, 1)),
            executed INTEGER NOT NULL DEFAULT 0 CHECK (executed IN (0, 1)),
            outcome TEXT,
            tool_calls INTEGER NOT NULL DEFAULT 0,
            correction INTEGER NOT NULL DEFAULT 0 CHECK (correction IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (version_id, run_id)
        );

        CREATE VIRTUAL TABLE skill_fts USING fts5(
            version_id UNINDEXED,
            slug,
            name,
            description,
            triggers,
            instructions
        );

        CREATE UNIQUE INDEX skill_one_active_version_idx ON skill_versions(skill_id)
            WHERE status = 'active';
        CREATE UNIQUE INDEX skill_global_slug_idx ON skills(slug) WHERE scope = 'global';
        CREATE INDEX skill_jobs_status_idx ON skill_jobs(status, created_at);
        CREATE INDEX workflow_scope_idx
            ON workflow_signatures(workspace_path, agent_id, created_at);
        CREATE INDEX skill_usage_run_idx ON skill_usage(run_id);

        CREATE TRIGGER skill_versions_no_delete
        BEFORE DELETE ON skill_versions
        BEGIN
            SELECT RAISE(ABORT, 'skill versions cannot be deleted');
        END;
        """,
    ),
    Migration(
        9,
        "evolution scars",
        """
        CREATE TABLE scars (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            scope TEXT NOT NULL CHECK (scope IN ('global', 'workspace', 'agent')),
            status TEXT NOT NULL CHECK (
                status IN (
                    'candidate', 'open', 'repair_proposed', 'guarded',
                    'healed', 'regressed', 'dismissed'
                )
            ),
            severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
            failure_signature TEXT NOT NULL,
            signature_hash TEXT NOT NULL,
            description TEXT NOT NULL,
            trigger_json TEXT NOT NULL,
            expected_behavior TEXT NOT NULL,
            detection TEXT NOT NULL DEFAULT 'explicit_correction',
            owner_agent_id TEXT,
            workspace_path TEXT,
            source_session_id TEXT NOT NULL REFERENCES sessions(id),
            source_run_id TEXT,
            repair_layer TEXT CHECK (
                repair_layer IS NULL OR repair_layer IN (
                    'semantic_memory', 'relationship_memory', 'episodic_memory',
                    'skill', 'policy_rule', 'context_rule', 'capability_requirement'
                )
            ),
            repair_reference TEXT,
            last_triggered_at TEXT NOT NULL,
            successful_guard_count INTEGER NOT NULL DEFAULT 0
                CHECK (successful_guard_count >= 0),
            regression_count INTEGER NOT NULL DEFAULT 0 CHECK (regression_count >= 0),
            dismissed_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (scope != 'workspace' OR workspace_path IS NOT NULL),
            CHECK (scope != 'agent' OR owner_agent_id IS NOT NULL)
        );

        CREATE TABLE scar_evidence (
            scar_id TEXT NOT NULL REFERENCES scars(id),
            event_id TEXT NOT NULL REFERENCES events(id),
            PRIMARY KEY (scar_id, event_id)
        );

        CREATE TABLE scar_repairs (
            id TEXT PRIMARY KEY,
            scar_id TEXT NOT NULL REFERENCES scars(id),
            version INTEGER NOT NULL,
            repair_layer TEXT NOT NULL CHECK (
                repair_layer IN (
                    'semantic_memory', 'relationship_memory', 'episodic_memory',
                    'skill', 'policy_rule', 'context_rule', 'capability_requirement'
                )
            ),
            base_hash TEXT NOT NULL DEFAULT '',
            proposal_json TEXT NOT NULL,
            rationale TEXT NOT NULL,
            deterministic_checks_json TEXT NOT NULL DEFAULT '[]',
            model_eval_report_json TEXT,
            risk TEXT NOT NULL CHECK (risk IN ('low', 'medium', 'high')),
            required_authority TEXT NOT NULL CHECK (
                required_authority IN (
                    'none', 'memory_write', 'skill_write', 'policy_write',
                    'context_write', 'plugin_write'
                )
            ),
            status TEXT NOT NULL CHECK (
                status IN ('proposed', 'promoted', 'rejected', 'superseded')
            ),
            previous_scar_status TEXT NOT NULL CHECK (
                previous_scar_status IN ('open', 'regressed')
            ),
            created_by TEXT NOT NULL CHECK (created_by IN ('automatic', 'user')),
            source_session_id TEXT NOT NULL REFERENCES sessions(id),
            created_at TEXT NOT NULL,
            decided_at TEXT,
            UNIQUE (scar_id, version)
        );

        CREATE INDEX scars_status_idx ON scars(status, severity);
        CREATE INDEX scars_signature_idx ON scars(signature_hash);
        CREATE INDEX scars_workspace_idx ON scars(workspace_path);
        CREATE INDEX scar_repairs_scar_idx ON scar_repairs(scar_id, version);

        CREATE TRIGGER scars_no_delete
        BEFORE DELETE ON scars
        BEGIN
            SELECT RAISE(ABORT, 'scars cannot be deleted');
        END;

        CREATE TRIGGER scar_repairs_no_delete
        BEFORE DELETE ON scar_repairs
        BEGIN
            SELECT RAISE(ABORT, 'scar repairs cannot be deleted');
        END;
        """,
    ),
    Migration(
        10,
        "declarative context and policy rules",
        """
        CREATE TABLE context_rules (
            id TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            condition_json TEXT NOT NULL,
            require_source_types_json TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('proposed', 'active', 'retired')),
            scar_id TEXT REFERENCES scars(id),
            source_session_id TEXT NOT NULL REFERENCES sessions(id),
            created_by TEXT NOT NULL CHECK (created_by IN ('automatic', 'user')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE policy_rules (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL CHECK (action IN ('deny', 'confirm')),
            scope TEXT NOT NULL DEFAULT 'shell_command',
            pattern TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('proposed', 'active', 'retired')),
            scar_id TEXT REFERENCES scars(id),
            source_session_id TEXT NOT NULL REFERENCES sessions(id),
            created_by TEXT NOT NULL CHECK (created_by IN ('automatic', 'user')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX context_rules_status_idx ON context_rules(status);
        CREATE INDEX policy_rules_status_idx ON policy_rules(status);

        CREATE TRIGGER context_rules_no_delete
        BEFORE DELETE ON context_rules
        BEGIN
            SELECT RAISE(ABORT, 'context rules cannot be deleted');
        END;

        CREATE TRIGGER policy_rules_no_delete
        BEFORE DELETE ON policy_rules
        BEGIN
            SELECT RAISE(ABORT, 'policy rules cannot be deleted');
        END;
        """,
    ),
    Migration(
        11,
        "isolated plugins",
        """
        CREATE TABLE plugins (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            active_version_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE plugin_versions (
            id TEXT PRIMARY KEY,
            plugin_id TEXT NOT NULL REFERENCES plugins(id),
            version TEXT NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            package_path TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            permissions_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('installed', 'retired')),
            created_at TEXT NOT NULL,
            UNIQUE (plugin_id, version)
        );
        CREATE TABLE plugin_proposals (
            id TEXT PRIMARY KEY,
            plugin_id TEXT,
            scar_id TEXT,
            status TEXT NOT NULL CHECK (
                status IN ('proposed', 'rejected', 'installed')
            ),
            package_path TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            source_session_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX plugin_versions_plugin_idx ON plugin_versions(plugin_id, version);
        CREATE INDEX plugin_proposals_status_idx ON plugin_proposals(status);
        CREATE TRIGGER plugin_versions_no_delete
        BEFORE DELETE ON plugin_versions
        BEGIN
            SELECT RAISE(ABORT, 'plugin versions cannot be deleted');
        END;
        """,
    ),
    Migration(
        12,
        "session execution modes",
        """
        ALTER TABLE sessions
            ADD COLUMN interaction_mode TEXT NOT NULL DEFAULT 'auto'
            CHECK (interaction_mode IN ('manual', 'auto', 'plan'));
        ALTER TABLE approvals
            ADD COLUMN allow_session INTEGER NOT NULL DEFAULT 0
            CHECK (allow_session IN (0, 1));
        ALTER TABLE approvals
            ADD COLUMN approval_scope TEXT NOT NULL DEFAULT 'once'
            CHECK (approval_scope IN ('once', 'session'));
        CREATE TABLE session_tool_grants (
            session_id TEXT NOT NULL REFERENCES sessions(id),
            tool_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, tool_name)
        );
        """,
    ),
    Migration(
        13,
        "user-forgettable memory records",
        """
        DROP TRIGGER memory_records_no_delete;
        """,
    ),
    Migration(
        14,
        "user-removable behavioral scars",
        """
        DROP TRIGGER scars_no_delete;
        DROP TRIGGER scar_repairs_no_delete;
        """,
    ),
    Migration(
        15,
        "durable session message queue",
        """
        CREATE TABLE session_queue (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            ordinal INTEGER NOT NULL,
            content TEXT NOT NULL,
            remember INTEGER NOT NULL DEFAULT 0 CHECK (remember IN (0, 1)),
            paste_spans_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            UNIQUE(session_id, ordinal)
        );
        CREATE INDEX session_queue_order_idx ON session_queue(session_id, ordinal);

        CREATE TABLE session_queue_state (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id),
            paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
            updated_at TEXT NOT NULL
        );
        """,
    ),
    Migration(
        16,
        "typed session queue entries",
        """
        ALTER TABLE session_queue
            ADD COLUMN purpose TEXT NOT NULL DEFAULT 'turn'
            CHECK (purpose IN ('turn', 'plan_note'));
        """,
    ),
)


class Database:
    """Own connection policy and monotonic migrations for one Hames database."""

    def __init__(self, path: Path, migrations: tuple[Migration, ...] = MIGRATIONS) -> None:
        self.path = path
        self.migrations = migrations

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "sha256",
            1,
            _sha256_sql,
            deterministic=True,
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def migrate(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = self.connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row["id"]): str(row["checksum"])
                for row in connection.execute("SELECT id, checksum FROM schema_migrations")
            }
            for migration in self.migrations:
                previous = applied.get(migration.id)
                if previous is not None:
                    if previous != migration.checksum:
                        raise MigrationError(f"migration {migration.id} checksum changed")
                    continue
                script = f"""
                BEGIN IMMEDIATE;
                {migration.sql}
                INSERT INTO schema_migrations(id, name, checksum, applied_at)
                VALUES (
                    {migration.id},
                    {self._quote(migration.name)},
                    {self._quote(migration.checksum)},
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                );
                COMMIT;
                """
                try:
                    connection.executescript(script)
                except sqlite3.Error as exc:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise MigrationError(f"migration {migration.id} failed: {exc}") from exc
        finally:
            connection.close()
        self.path.chmod(0o600)

    @staticmethod
    def _quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"
