"""Local-time schedules, durable claims, and isolated scheduled conversations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hames.agent import AgentExecution
from hames.agent_execution import resolve_agent_execution
from hames.database import Database
from hames.ledger import new_id

if TYPE_CHECKING:
    from hames.runtime import RunManager

log = logging.getLogger(__name__)


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class AutomationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    instructions: str = Field(min_length=1, max_length=16000)
    working_directory: str = ""
    agent_id: str = "default"
    provider: str = ""
    model: str = ""
    reasoning_effort: str = ""
    frequency: Literal["once", "daily", "weekly"] = "daily"
    time: str = "10:00"
    timezone: str = "UTC"
    weekdays: list[int] = Field(default_factory=lambda: [0], max_length=7)
    date: str = ""
    enabled: bool = False
    catch_up: bool = True
    retries: int = Field(default=0, ge=0, le=2)
    notify: Literal["results", "failures", "off"] = "results"

    @model_validator(mode="after")
    def validate_schedule(self) -> AutomationDefinition:
        self.title = self.title.strip()
        self.instructions = self.instructions.strip()
        if not self.title or not self.instructions:
            raise ValueError("Name and instructions cannot be blank")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Choose a valid timezone, such as America/Halifax") from exc
        try:
            parsed = datetime.strptime(self.time, "%H:%M")
        except ValueError as exc:
            raise ValueError("Choose a time in HH:MM format") from exc
        self.time = parsed.strftime("%H:%M")
        if self.frequency == "weekly" and not self.weekdays:
            raise ValueError("Choose at least one weekday")
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("Weekdays must be Monday (0) through Sunday (6)")
        self.weekdays = sorted(set(self.weekdays))
        if self.frequency == "once":
            try:
                datetime.strptime(self.date, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("Choose a date for the one-time run") from exc
        return self


def next_occurrence(spec: AutomationDefinition, after: datetime) -> datetime | None:
    """One occurrence per local date; gaps move forward, repeated hours use first fold."""
    zone = ZoneInfo(spec.timezone)
    local = after.astimezone(zone)
    hour, minute = map(int, spec.time.split(":"))
    for offset in range(370):
        day = local.date() + timedelta(days=offset)
        if spec.frequency == "once" and day.isoformat() != spec.date:
            continue
        if spec.frequency == "weekly" and day.weekday() not in spec.weekdays:
            continue
        wall = datetime(day.year, day.month, day.day, hour, minute)
        for gap in range(181):
            candidate = (wall + timedelta(minutes=gap)).replace(tzinfo=zone, fold=0)
            utc = candidate.astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) == candidate.replace(tzinfo=None):
                if utc > after:
                    return utc
                break
    return None


class AutomationStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            return [
                dict(
                    json.loads(row["definition"]),
                    id=row["id"],
                    next_run=row["next_run"],
                    updated_at=row["updated_at"],
                )
                for row in db.execute("SELECT * FROM automations ORDER BY updated_at DESC")
            ]

    def get(self, automation_id: str) -> dict[str, Any]:
        return (
            next((item for item in self.list() if item["id"] == automation_id), None)
            or self._missing()
        )

    @staticmethod
    def _missing() -> Any:
        raise KeyError("Automation not found")

    def save(self, spec: AutomationDefinition, automation_id: str | None = None) -> dict[str, Any]:
        if automation_id:
            self.get(automation_id)
        automation_id = automation_id or new_id()
        due = next_occurrence(spec, datetime.now(UTC)) if spec.enabled else None
        if spec.enabled and due is None:
            raise ValueError("Choose a future date for this automation")
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO automations VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "definition=excluded.definition,next_run=excluded.next_run,updated_at=excluded.updated_at",
                (
                    automation_id,
                    spec.model_dump_json(),
                    due.isoformat() if due else None,
                    timestamp(),
                ),
            )
            if not spec.enabled:
                db.execute(
                    "UPDATE automation_runs SET status='skipped',message='Automation paused',"
                    "finished_at=? WHERE automation_id=? AND status='pending'",
                    (timestamp(), automation_id),
                )
        return self.get(automation_id)

    def history(self, automation_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT * FROM automation_runs "
                + ("WHERE automation_id=? " if automation_id else "")
                + "ORDER BY created_at DESC LIMIT 100",
                (automation_id,) if automation_id else (),
            )
            return [dict(row) for row in rows]

    def work(self) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM automation_runs WHERE status IN ('pending','running') OR "
                    "(notified=0 AND status IN ('completed','failed','cancelled','interrupted')) "
                    "ORDER BY created_at"
                )
            ]

    def delete(self, automation_id: str) -> None:
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM automation_runs WHERE automation_id=? AND status='running'",
                (automation_id,),
            ).fetchone():
                raise ValueError("Stop the active run in its chat before deleting this automation")
            db.execute("DELETE FROM automations WHERE id=?", (automation_id,))
            db.commit()

    def enqueue(self, automation_id: str, *, scheduled: bool = False) -> str | None:
        now = timestamp()
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
            if row is None:
                raise KeyError("Automation not found")
            spec = AutomationDefinition.model_validate_json(row["definition"])
            if scheduled and (not spec.enabled or not row["next_run"] or row["next_run"] > now):
                return None
            active = db.execute(
                "SELECT 1 FROM automation_runs WHERE automation_id=? "
                "AND status IN ('pending','running')",
                (automation_id,),
            ).fetchone()
            if active and not scheduled:
                raise ValueError("This automation already has a pending or running task")
            missed = (
                scheduled
                and not spec.catch_up
                and datetime.fromisoformat(row["next_run"])
                < datetime.now(UTC) - timedelta(minutes=5)
            )
            status = "skipped" if active or missed else "pending"
            job = new_id()
            db.execute(
                "INSERT INTO automation_runs(id,automation_id,due_at,status,created_at,"
                "message,finished_at,definition) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    job,
                    automation_id,
                    now,
                    status,
                    now,
                    "Previous run still active" if active else "Missed schedule" if missed else "",
                    now if status == "skipped" else None,
                    spec.model_dump_json(),
                ),
            )
            if scheduled:
                due = next_occurrence(spec, datetime.now(UTC))
                if spec.frequency == "once":
                    spec.enabled = False
                    due = None
                db.execute(
                    "UPDATE automations SET definition=?,next_run=? WHERE id=?",
                    (spec.model_dump_json(), due.isoformat() if due else None, automation_id),
                )
            db.commit()
            return job


class AutomationScheduler:
    def __init__(self, runs: RunManager, base_url: str) -> None:
        self.runs = runs
        self.store = runs.automations
        self.base_url = base_url
        self.task: asyncio.Task[None] | None = None
        self.notifications: set[asyncio.Task[None]] = set()
        self.native_available = False

    async def refresh_notifications(self) -> None:
        self.native_available = False
        if not (
            shutil.which("notify-send")
            and shutil.which("busctl")
            and os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        ):
            return
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "busctl",
                "--user",
                "call",
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "NameHasOwner",
                "s",
                "org.freedesktop.Notifications",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await asyncio.wait_for(process.communicate(), 3)
            self.native_available = process.returncode == 0 and output.strip() == b"b true"
        except (OSError, TimeoutError):
            pass
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()

    async def start(self) -> None:
        await self.refresh_notifications()
        # Reconcile terminal events first; never repeat potentially executed side effects.
        for job in await asyncio.to_thread(self.store.work):
            if job["status"] != "running":
                continue
            events = self.runs.ledger.list_run_events(job["run_id"]) if job["run_id"] else []
            terminal = next(
                (
                    e
                    for e in reversed(events)
                    if e.type in {"run.completed", "run.failed", "run.cancelled"}
                ),
                None,
            )
            await self.finish(
                job,
                terminal.type.removeprefix("run.") if terminal else "interrupted",
                str(terminal.payload.get("message", "Completed"))
                if terminal
                else "Gateway stopped during this run; review its chat before retrying",
            )
        self.task = asyncio.create_task(self.loop(), name="automation-scheduler")

    async def close(self) -> None:
        tasks = ([self.task] if self.task else []) + list(self.notifications)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def loop(self) -> None:
        while True:
            try:
                await self.refresh_notifications()
                await self.tick()
            except Exception:
                log.exception("Automation scheduler tick failed")
            await asyncio.sleep(10)

    async def tick(self) -> None:
        for spec in await asyncio.to_thread(self.store.list):
            await asyncio.to_thread(self.store.enqueue, spec["id"], scheduled=True)
        for job in await asyncio.to_thread(self.store.work):
            if job["status"] == "pending" and job["due_at"] <= timestamp():
                await self.launch(job)
            elif job["status"] == "running" and job["run_id"]:
                events = await asyncio.to_thread(self.runs.ledger.list_run_events, job["run_id"])
                terminal = next(
                    (
                        e
                        for e in reversed(events)
                        if e.type in {"run.completed", "run.failed", "run.cancelled"}
                    ),
                    None,
                )
                if terminal:
                    status = terminal.type.removeprefix("run.")
                    message = (
                        "Completed"
                        if status == "completed"
                        else str(terminal.payload.get("message", "Run cancelled"))
                    )
                    await self.finish(
                        job, status, message, retryable=bool(terminal.payload.get("retryable"))
                    )
            elif (
                job["status"] in {"completed", "failed", "interrupted", "cancelled"}
                and not job["notified"]
            ):
                await self.notify(job)

    async def launch(self, job: dict[str, Any]) -> None:
        with self.store.database.connect() as db:
            claimed = db.execute(
                "UPDATE automation_runs SET status='running' WHERE id=? AND status='pending'",
                (job["id"],),
            ).rowcount
        if not claimed:
            return
        try:
            spec = AutomationDefinition.model_validate_json(job["definition"])
            self.runs.agents.load(spec.agent_id)
            if spec.working_directory:
                directory = Path(spec.working_directory)
            else:
                # Keep task files outside protected harness state, without registering a project.
                root = self.runs.paths.root
                directory = root.parent / f"{root.name}-automation-files" / job["automation_id"]
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.runs.controls.grant_trust(directory)
            if self.runs.controls.get_trust(directory) is None:
                raise ValueError("Workspace is no longer trusted")
            provider, model, effort, window, source = await resolve_agent_execution(
                AgentExecution(
                    provider=spec.provider, model=spec.model, reasoning_effort=spec.reasoning_effort
                ),
                self.runs.providers,
                self.runs.config,
            )
            session = await asyncio.to_thread(
                self.runs.ledger.create_session,
                working_directory=directory,
                agent_id=spec.agent_id,
                provider=provider,
                model=model,
                reasoning_effort=effort,
                context_window_tokens=window,
                context_window_source=source,
                title=f"{spec.title} · "
                f"{datetime.now(ZoneInfo(spec.timezone)).strftime('%b %d %H:%M')}",
            )
            run_id = new_id()
            with self.store.database.connect() as db:
                db.execute(
                    "UPDATE automation_runs SET session_id=?,run_id=? WHERE id=?",
                    (session.id, run_id, job["id"]),
                )
            await self.runs.start(
                session.id,
                "Scheduled task:\n"
                + spec.instructions
                + "\n\nStay within this task's authorization. Reading/reporting does not authorize "
                "sending messages or changing external accounts. "
                "Do not create or modify schedules from a scheduled run. "
                "Report missing access instead of inventing results.",
                run_id=run_id,
            )
        except Exception as exc:
            await self.finish(job, "failed", str(exc))

    async def finish(
        self, job: dict[str, Any], status: str, message: str, *, retryable: bool = False
    ) -> None:
        spec = self.store.get(job["automation_id"])
        with self.store.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE automation_runs SET status=?,message=?,finished_at=? "
                "WHERE id=? AND status='running'",
                (status, message[:1000], timestamp(), job["id"]),
            ).rowcount
            if (
                changed
                and status == "failed"
                and retryable
                and spec["enabled"]
                and job["attempt"] < spec["retries"]
            ):
                db.execute(
                    "INSERT INTO automation_runs(id,automation_id,due_at,status,"
                    "attempt,created_at,definition) "
                    "VALUES(?,?,?,'pending',?,?,?)",
                    (
                        new_id(),
                        job["automation_id"],
                        (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
                        job["attempt"] + 1,
                        timestamp(),
                        job["definition"],
                    ),
                )
            db.commit()

    async def notify(self, job: dict[str, Any]) -> None:
        spec = self.store.get(job["automation_id"])
        if (
            spec["notify"] != "off"
            and (spec["notify"] == "results" or job["status"] != "completed")
            and self.native_available
        ):
            task = asyncio.create_task(self.desktop_notice(spec["title"], job))
            self.notifications.add(task)
            task.add_done_callback(self.notifications.discard)
        with self.store.database.connect() as db:
            db.execute("UPDATE automation_runs SET notified=1 WHERE id=?", (job["id"],))

    async def desktop_notice(self, title: str, job: dict[str, Any]) -> None:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                "notify-send",
                "--app-name=Hames",
                "--icon=appointment-soon",
                "--action=open=Open run",
                "--wait",
                "--expire-time=15000",
                "--",
                title,
                "Automation completed. Open Hames to view results."
                if job["status"] == "completed"
                else "Automation needs attention. Open Hames to review it.",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
            if output.strip() == b"open" and job["session_id"]:
                opener = await asyncio.create_subprocess_exec(
                    "xdg-open",
                    f"{self.base_url}/chat/{job['session_id']}",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(opener.wait(), timeout=10)
        except (OSError, TimeoutError):
            log.warning("Desktop automation notification unavailable")
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
