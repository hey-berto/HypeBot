from __future__ import annotations

import errno
import fcntl
import json
import os
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Self


class LeaseAlreadyOwned(RuntimeError):
    """Raised when a second authoritative Phase 2 process is attempted."""


def _process_start_identity(pid: int) -> str:
    """Return a PID-reuse-resistant process identity on macOS and Linux."""
    command = ["/bin/ps", "-o", "lstart=", "-p", str(pid)]
    return subprocess.check_output(command, text=True).strip()


class ExclusiveProcessLease:
    """Lifetime OS lease with auditable owner metadata.

    ``flock`` is released by the kernel on exit, including SIGKILL.  The JSON
    owner record is diagnostic only; it never grants ownership by itself.
    """

    def __init__(self, path: str | Path, *, role: str, epoch_id: str) -> None:
        self.path = Path(path).resolve()
        self.role = role
        self.epoch_id = epoch_id
        self._handle: IO[str] | None = None
        self.owner: dict[str, object] | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> Self:
        if self.held:
            self.assert_owned()
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            observed = handle.read().strip() or "owner metadata unavailable"
            handle.close()
            raise LeaseAlreadyOwned(
                f"exclusive {self.role} lease is already owned: {observed}"
            ) from exc
        owner = {
            "epoch_id": self.epoch_id,
            "role": self.role,
            "pid": os.getpid(),
            "process_group": os.getpgrp(),
            "process_started_at": _process_start_identity(os.getpid()),
            "lease_acquired_at": datetime.now(UTC).isoformat(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(owner, sort_keys=True, separators=(",", ":")))
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        self.owner = owner
        return self

    def assert_owned(self) -> None:
        if self._handle is None:
            raise LeaseAlreadyOwned(f"{self.role} lease is not held")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LeaseAlreadyOwned(f"{self.role} lease ownership was lost") from exc

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


def process_group_members(process_group: int) -> tuple[int, ...]:
    output = subprocess.check_output(
        ["/bin/ps", "-ax", "-o", "pid=,pgid=,stat="], text=True
    )
    members = []
    for line in output.splitlines():
        fields = line.split()
        if (
            len(fields) == 3
            and int(fields[1]) == process_group
            and not fields[2].startswith("Z")
        ):
            members.append(int(fields[0]))
    return tuple(sorted(members))


def terminate_process_group(
    process_group: int,
    *,
    term_timeout_seconds: float = 10.0,
    poll_seconds: float = 0.05,
) -> dict[str, object]:
    """Terminate one exact child process group and prove it is gone."""
    initial = process_group_members(process_group)
    if not initial:
        return {
            "process_group": process_group,
            "initial_members": [],
            "final_members": [],
        }
    os.killpg(process_group, signal.SIGTERM)
    deadline = time.monotonic() + term_timeout_seconds
    while time.monotonic() < deadline and process_group_members(process_group):
        time.sleep(poll_seconds)
    remaining = process_group_members(process_group)
    if remaining:
        os.killpg(process_group, signal.SIGKILL)
        deadline = time.monotonic() + term_timeout_seconds
        while time.monotonic() < deadline and process_group_members(process_group):
            time.sleep(poll_seconds)
    final = process_group_members(process_group)
    if final:
        raise RuntimeError(
            f"old Phase 2 worker group {process_group} remains alive: {final}"
        )
    return {
        "process_group": process_group,
        "initial_members": list(initial),
        "final_members": [],
    }


@dataclass(frozen=True)
class SupervisorEvent:
    event: str
    timestamp: str
    supervisor_pid: int
    details: dict[str, object]


class SingleWriterSupervisor:
    """External owner that never relaunches before proving old-group death."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        cwd: str | Path,
        supervisor_lease: ExclusiveProcessLease,
        event_sink: Callable[[SupervisorEvent], None],
        environment: dict[str, str] | None = None,
        restart_delay_seconds: float = 1.0,
        prelaunch: Callable[[], None] | None = None,
        stdout_path: str | Path | None = None,
        stderr_path: str | Path | None = None,
    ) -> None:
        self.command = tuple(command)
        self.cwd = Path(cwd).resolve()
        self.supervisor_lease = supervisor_lease
        self.event_sink = event_sink
        self.environment = environment
        self.restart_delay_seconds = restart_delay_seconds
        self.prelaunch = prelaunch
        self.stdout_path = Path(stdout_path).resolve() if stdout_path else None
        self.stderr_path = Path(stderr_path).resolve() if stderr_path else None
        self.child: subprocess.Popen[bytes] | None = None
        self.stop_requested = False

    def _emit(self, event: str, **details: object) -> None:
        self.event_sink(
            SupervisorEvent(
                event=event,
                timestamp=datetime.now(UTC).isoformat(),
                supervisor_pid=os.getpid(),
                details=details,
            )
        )

    def request_stop(self, *_: object) -> None:
        self.stop_requested = True

    def launch(self) -> subprocess.Popen[bytes]:
        self.supervisor_lease.assert_owned()
        if self.child is not None:
            if self.child.poll() is None:
                raise RuntimeError("refusing to overlap an existing worker")
            evidence = terminate_process_group(self.child.pid)
            self._emit("OLD_WORKER_GROUP_CONFIRMED_DEAD", **evidence)
        if self.prelaunch is not None:
            self.prelaunch()
        stdout = self.stdout_path.open("ab", buffering=0) if self.stdout_path else None
        stderr = self.stderr_path.open("ab", buffering=0) if self.stderr_path else None
        try:
            child = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout or subprocess.DEVNULL,
                stderr=stderr or subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            if stdout is not None:
                stdout.close()
            if stderr is not None:
                stderr.close()
        self.child = child
        self._emit(
            "WORKER_STARTED",
            worker_pid=child.pid,
            worker_process_group=child.pid,
            worker_started_at=_process_start_identity(child.pid),
        )
        return child

    def stop_child(self) -> None:
        if self.child is None:
            return
        evidence = terminate_process_group(self.child.pid)
        try:
            status = self.child.wait(timeout=1.0)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "worker group is gone but child wait did not resolve"
            ) from exc
        self._emit("WORKER_STOPPED", exit_status=status, **evidence)

    def run(self) -> None:
        self.supervisor_lease.acquire()
        prior_term = signal.signal(signal.SIGTERM, self.request_stop)
        prior_int = signal.signal(signal.SIGINT, self.request_stop)
        try:
            self._emit(
                "SUPERVISOR_STARTED", lease_owner=self.supervisor_lease.owner or {}
            )
            while not self.stop_requested:
                child = self.launch()
                while not self.stop_requested and child.poll() is None:
                    time.sleep(0.1)
                self.stop_child()
                if not self.stop_requested:
                    self._emit("RESTART_DELAY", seconds=self.restart_delay_seconds)
                    time.sleep(self.restart_delay_seconds)
        finally:
            self.stop_child()
            self.supervisor_lease.release()
            signal.signal(signal.SIGTERM, prior_term)
            signal.signal(signal.SIGINT, prior_int)


def lease_exit_code(exc: BaseException) -> int:
    if isinstance(exc, LeaseAlreadyOwned):
        return errno.EWOULDBLOCK
    return 1
