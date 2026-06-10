from ic_data_bot.health import ReadyState, liveness, readiness

def test_liveness_always_ok():
    assert liveness() == (200, "ok")

def test_readiness_flips_when_ready():
    s = ReadyState()
    assert readiness(s) == (503, "not ready")
    s.discord_ready = True
    assert readiness(s) == (503, "not ready")   # noyau pas encore chargé
    s.core_loaded = True
    assert readiness(s) == (200, "ready")

def test_ready_property():
    s = ReadyState(discord_ready=True, core_loaded=True)
    assert s.ready is True
    assert ReadyState().ready is False
