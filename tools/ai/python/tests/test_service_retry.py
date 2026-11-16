"""Recovery budgets survive short successes and never replay business requests."""

from sico_ui.service_retry import ServiceRetry


def test_bounded_backoff_and_stable_success_reset():
    now = [0]
    retry = ServiceRetry(limit=6, delay=1, maximum=8, stable_seconds=30,
                         clock=lambda: now[0])
    starts = []
    for delay in (1, 2, 4, 8, 8, 8):
        assert retry.start()
        starts.append(now[0])
        retry.healthy()
        now[0] += .5
        retry.healthy()
        retry.unhealthy()
        assert not retry.start()
        now[0] += delay - .5
    assert starts == [0, 1, 3, 7, 15, 23]
    assert retry.exhausted and not retry.start()
    retry.healthy()
    now[0] += 29
    retry.healthy()
    assert retry.exhausted
    now[0] += 1
    retry.healthy()
    assert retry.attempts == 0 and not retry.start()
    now[0] += 1
    assert retry.start()
    retry.reset()
    assert retry.start() and retry.attempts == 1


def test_initial_delay_and_outage_cancel_the_stable_window():
    now = [0]
    retry = ServiceRetry(limit=1, delay=3, immediate=False, clock=lambda: now[0])
    assert not retry.start()
    now[0] = 3
    assert retry.start()
    retry.healthy()
    now[0] = 32
    retry.unhealthy()
    retry.healthy()
    now[0] = 34
    retry.healthy()
    assert retry.exhausted
