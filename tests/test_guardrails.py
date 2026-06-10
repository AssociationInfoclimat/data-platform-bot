from ic_data_bot.guardrails import RateLimiter, DailyBudget, is_allowed_channel

def test_is_allowed_channel():
    assert is_allowed_channel(123, 123)
    assert not is_allowed_channel(999, 123)

def test_rate_limiter_blocks_after_limit():
    t = {"now": 0.0}
    rl = RateLimiter(max_events=2, window_seconds=60, clock=lambda: t["now"])
    assert rl.allow("u1")
    assert rl.allow("u1")
    assert not rl.allow("u1")          # 3e dans la fenêtre → refusé
    t["now"] = 61.0
    assert rl.allow("u1")              # fenêtre écoulée → ok
    assert rl.allow("u2")              # autre user indépendant

def test_daily_budget_persists_and_resets(tmp_path):
    path = tmp_path / "budget.json"
    day = {"d": "2026-06-09"}
    b = DailyBudget(path, limit=100, today_fn=lambda: day["d"])
    assert b.has_budget()
    b.add(80)
    assert b.remaining() == 20
    assert b.has_budget()
    b.add(50)
    assert not b.has_budget()          # 130 > 100
    # rechargé depuis le disque
    b2 = DailyBudget(path, limit=100, today_fn=lambda: day["d"])
    assert b2.remaining() == 0
    # nouveau jour → reset
    day["d"] = "2026-06-10"
    b3 = DailyBudget(path, limit=100, today_fn=lambda: day["d"])
    assert b3.remaining() == 100
