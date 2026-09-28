"""Shared types for dynamic-verification backends."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from lantern_analysis.secrets import redact
from lantern_worker.dynamic.canaries import CanaryHit, CanarySet
from lantern_worker.dynamic.plan import DynamicSettings, Step

OUTPUT_TAIL = 2000
MAX_OUTPUT_SCAN = 4 << 20


@dataclass(frozen=True)
class Limits:
    """Hard limits for one verification run.

    ``wall_clock_s`` bounds the whole run; each step also gets ``step_timeout_s``. The Docker
    backend enforces CPU, memory, process count, and disk through the container runtime (a
    read-only root filesystem plus size-capped tmpfs mounts). The local fixture backend
    enforces wall clock, CPU seconds, and file size with rlimits.
    """

    wall_clock_s: float = 600.0
    step_timeout_s: float = 240.0
    install_timeout_s: float = 600.0
    cpus: float = 1.0
    cpu_seconds: int = 600
    memory_mb: int = 1024
    disk_mb: int = 512
    pids: int = 256
    max_file_mb: int = 256


@dataclass
class StepOutcome:
    mode: str
    argv: list[str]
    exit_code: int | None
    duration_s: float
    timed_out: bool = False
    requests: int = 0
    output_tail: str = ""
    routes: dict[str, Any] | None = None
    error: str = ""
    output_canaries: list[CanaryHit] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "argv": self.argv,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 2),
            "timed_out": self.timed_out,
            "requests": self.requests,
            "output_tail": self.output_tail,
            "routes": self.routes,
            "error": self.error,
            "output_canaries": [h.to_dict() for h in self.output_canaries],
        }


def tail(text: str | bytes | None, limit: int = OUTPUT_TAIL) -> str:
    """The redacted end of a process's output. Output is bounded before it is stored."""
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return redact(text[-limit:])


def output_hits(canaries: CanarySet, output: str | bytes | None) -> list[CanaryHit]:
    """Canaries in a process's stdout and stderr: evidence for log sinks."""
    if not output:
        return []
    data = output.encode("utf-8", "replace") if isinstance(output, str) else output
    hits: set[CanaryHit] = set()
    for start in range(0, min(len(data), MAX_OUTPUT_SCAN), 1 << 20):
        hits |= canaries.scan(data[start : start + (1 << 20)], "process-output")
    return sorted(hits)


class Session(Protocol):
    capture: Path

    def run(
        self, step: Step, static_routes: list[dict[str, Any]], budget_s: float
    ) -> StepOutcome: ...


class Backend(Protocol):
    name: str

    def open(
        self,
        repo: Path,
        settings: DynamicSettings,
        canaries: CanarySet,
        workdir: Path,
        limits: Limits,
    ) -> AbstractContextManager[Session]: ...


@dataclass
class SetupError(Exception):
    """The backend could not prepare the run (dependency install failed, image build failed)."""

    stage: str
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.stage}: {self.detail}"


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def iter_lines(path: Path) -> Iterator[str]:
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            yield from fh
