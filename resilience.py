"""
resilience.py

Small, dependency-free building blocks used to keep the system cheap and
degrade gracefully when an external dependency (the Groq API) misbehaves:

    SlidingWindowBudget  hard cap on calls per minute AND per day
    CircuitBreaker       stop calling a failing dependency for a cooldown
    TTLCache             reuse answers for identical inputs (zero API cost)
    KeyedRateLimiter     per-client request limit for the HTTP API

All classes are thread-safe (FastAPI runs sync endpoints in a thread pool)
and take an injectable clock so they are deterministic under test.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Hashable

Clock = Callable[[], float]

MINUTE_S = 60.0
DAY_S = 86_400.0


class SlidingWindowBudget:
    """Allow at most ``per_minute`` and ``per_day`` acquisitions (sliding windows)."""

    def __init__(self, per_minute: int, per_day: int, clock: Clock = time.time) -> None:
        self._per_minute = per_minute
        self._per_day = per_day
        self._clock = clock
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    def _evict(self, now: float) -> None:
        while self._stamps and now - self._stamps[0] >= DAY_S:
            self._stamps.popleft()

    def _in_last_minute(self, now: float) -> int:
        return sum(1 for t in self._stamps if now - t < MINUTE_S)

    def try_acquire(self) -> bool:
        """Record one call if both budgets allow it; return False (no record) if not."""
        now = self._clock()
        with self._lock:
            self._evict(now)
            if len(self._stamps) >= self._per_day or self._in_last_minute(now) >= self._per_minute:
                return False
            self._stamps.append(now)
            return True

    def snapshot(self) -> dict[str, int]:
        """Current usage, for the /health endpoint."""
        now = self._clock()
        with self._lock:
            self._evict(now)
            return {
                "used_last_minute": self._in_last_minute(now),
                "limit_per_minute": self._per_minute,
                "used_last_day": len(self._stamps),
                "limit_per_day": self._per_day,
            }


class CircuitBreaker:
    """Open after ``failure_threshold`` consecutive failures; half-open after cooldown."""

    def __init__(self, failure_threshold: int, cooldown_s: float, clock: Clock = time.monotonic) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """True if a call may proceed (closed, or cooldown elapsed -> one trial)."""
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at >= self._cooldown:
                # Half-open: let one trial through; a failure re-opens immediately.
                self._opened_at = None
                self._failures = self._threshold - 1
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened_at = self._clock()

    @property
    def state(self) -> str:
        """'open' while cooling down, else 'closed'."""
        with self._lock:
            return "open" if self._opened_at is not None else "closed"


class TTLCache[V]:
    """Bounded LRU cache whose entries expire after ``ttl_s`` seconds."""

    def __init__(self, max_size: int, ttl_s: float, clock: Clock = time.monotonic) -> None:
        self._max = max_size
        self._ttl = ttl_s
        self._clock = clock
        self._data: OrderedDict[Hashable, tuple[float, V]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> V | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            stored_at, value = item
            if self._clock() - stored_at >= self._ttl:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: Hashable, value: V) -> None:
        if self._max <= 0:
            return
        with self._lock:
            self._data[key] = (self._clock(), value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


class KeyedRateLimiter:
    """Per-key sliding-window limiter (e.g. per client IP)."""

    def __init__(self, limit: int, window_s: float = MINUTE_S, clock: Clock = time.monotonic) -> None:
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """Record a hit. Return None if allowed, else seconds until the next slot."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return max(0.0, self._window - (now - hits[0]))
            hits.append(now)
            return None
