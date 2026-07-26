"""可信高频伺服：策略只接收 Converged / Recoverable / Abort。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class ServoStatus(str, Enum):
    CONVERGED = "converged"
    RECOVERABLE = "recoverable"
    ABORT = "abort"


@dataclass(frozen=True, slots=True)
class ServoOutcome:
    status: ServoStatus
    ticks: int
    final_error: float
    reason: str = ""

    def __post_init__(self) -> None:
        if self.ticks < 0:
            raise ValueError("ticks must be >= 0")
        if self.status is not ServoStatus.CONVERGED and not self.reason.strip():
            raise ValueError("non-converged outcomes require a reason")


ObserveError = Callable[[], float]
Correct = Callable[[float], None]
Verify = Callable[[], bool]


@dataclass(slots=True)
class ServoController:
    """在可信 runtime 内执行 observe → bounded correction → verify。"""

    observe_error: ObserveError
    correct: Correct
    verify: Verify
    max_ticks: int = 50
    error_tolerance: float = 1e-3

    def __post_init__(self) -> None:
        if self.max_ticks < 1:
            raise ValueError("max_ticks must be >= 1")
        if self.error_tolerance < 0:
            raise ValueError("error_tolerance must be >= 0")

    def run(self) -> ServoOutcome:
        last_error = float("inf")
        for tick in range(1, self.max_ticks + 1):
            try:
                error = float(self.observe_error())
            except Exception as exc:
                return ServoOutcome(
                    status=ServoStatus.ABORT,
                    ticks=tick,
                    final_error=last_error if last_error != float("inf") else -1.0,
                    reason=f"observe failed: {type(exc).__name__}: {exc}",
                )
            last_error = abs(error)
            if last_error <= self.error_tolerance and self.verify():
                return ServoOutcome(
                    status=ServoStatus.CONVERGED,
                    ticks=tick,
                    final_error=last_error,
                )
            try:
                self.correct(error)
            except Exception as exc:
                return ServoOutcome(
                    status=ServoStatus.RECOVERABLE,
                    ticks=tick,
                    final_error=last_error,
                    reason=f"correct failed: {type(exc).__name__}: {exc}",
                )
            try:
                if self.verify() and abs(float(self.observe_error())) <= self.error_tolerance:
                    return ServoOutcome(
                        status=ServoStatus.CONVERGED,
                        ticks=tick,
                        final_error=abs(float(self.observe_error())),
                    )
            except Exception as exc:
                return ServoOutcome(
                    status=ServoStatus.RECOVERABLE,
                    ticks=tick,
                    final_error=last_error,
                    reason=f"verify failed: {type(exc).__name__}: {exc}",
                )
        return ServoOutcome(
            status=ServoStatus.ABORT,
            ticks=self.max_ticks,
            final_error=last_error if last_error != float("inf") else -1.0,
            reason="servo budget exhausted",
        )
