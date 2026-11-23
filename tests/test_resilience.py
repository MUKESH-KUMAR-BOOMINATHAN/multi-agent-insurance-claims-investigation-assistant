"""Budget / breaker / cache / rate-limiter behaviour, driven by a fake clock."""

from resilience import CircuitBreaker, KeyedRateLimiter, SlidingWindowBudget, TTLCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_budget_blocks_after_per_minute_limit_then_recovers():
    clock = FakeClock()
    budget = SlidingWindowBudget(per_minute=2, per_day=100, clock=clock)
    assert budget.try_acquire() and budget.try_acquire()
    assert budget.try_acquire() is False  # third call in the same minute is refused
    clock.now += 61
    assert budget.try_acquire() is True  # window slid


def test_budget_enforces_daily_cap_even_when_minute_window_is_clear():
    clock = FakeClock()
    budget = SlidingWindowBudget(per_minute=100, per_day=3, clock=clock)
    for _ in range(3):
        assert budget.try_acquire()
        clock.now += 120  # spread out so the per-minute limit never triggers
    assert budget.try_acquire() is False
    clock.now += 86_400
    assert budget.try_acquire() is True


def test_refused_acquire_is_not_recorded():
    clock = FakeClock()
    budget = SlidingWindowBudget(per_minute=1, per_day=10, clock=clock)
    budget.try_acquire()
    budget.try_acquire()  # refused
    assert budget.snapshot()["used_last_day"] == 1


def test_breaker_opens_after_threshold_and_half_opens_after_cooldown():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, cooldown_s=30, clock=clock)
    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state == "open" and breaker.allow() is False
    clock.now += 31
    assert breaker.allow() is True  # one trial after the cooldown
    breaker.record_failure()  # the trial failed -> straight back to open
    assert breaker.state == "open"


def test_breaker_success_resets_failure_count():
    breaker = CircuitBreaker(failure_threshold=2, cooldown_s=30, clock=FakeClock())
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state == "closed"


def test_cache_expires_entries_and_evicts_least_recently_used():
    clock = FakeClock()
    cache: TTLCache[str] = TTLCache(max_size=2, ttl_s=10, clock=clock)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.get("a")  # refresh a
    cache.set("c", "3")  # evicts b
    assert cache.get("b") is None and cache.get("a") == "1"
    clock.now += 11
    assert cache.get("a") is None


def test_keyed_rate_limiter_isolated_per_key_and_reports_retry_after():
    clock = FakeClock()
    limiter = KeyedRateLimiter(limit=2, window_s=60, clock=clock)
    assert limiter.check("1.1.1.1") is None and limiter.check("1.1.1.1") is None
    retry = limiter.check("1.1.1.1")
    assert retry is not None and 0 < retry <= 60
    assert limiter.check("2.2.2.2") is None  # another client is unaffected
    clock.now += 61
    assert limiter.check("1.1.1.1") is None
