"""The newest browser test of this site, from the public GitHub API (management/e2e.py): the HTTP is mocked."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests
from django.core.exceptions import ImproperlyConfigured

from management import e2e

REPO = "https://api.github.com/repos/fpgas-online/fpgas.online-e2e-tests"


def job(name, conclusion, at="2026-10-11T05:34:28Z", run=1, status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion, "completed_at": at,
            "html_url": f"https://github.com/fpgas-online/fpgas.online-e2e-tests/actions/runs/{run}/job/{run}0"}


class Github:
    """A fake `requests.get` answering the run list and each run's jobs; `calls` records the URLs asked."""

    def __init__(self, runs, status=200, jobs_status=200):
        self.runs, self.status, self.jobs_status, self.calls, self.timeouts = runs, status, jobs_status, [], []

    def __call__(self, url, params=None, timeout=None, headers=None):
        self.calls.append(url)
        self.timeouts.append(timeout)
        if url.endswith("/runs"):
            return SimpleNamespace(status_code=self.status, json=lambda: {
                "workflow_runs": [{"id": run_id} for run_id in self.runs]})
        run_id = int(url.split("/runs/")[1].split("/")[0])
        return SimpleNamespace(status_code=self.jobs_status, json=lambda: {"jobs": self.runs[run_id]})


@pytest.fixture
def github(monkeypatch):
    def install(runs, **kw):
        fake = Github(runs, **kw)
        monkeypatch.setattr("management.e2e.requests.get", fake)
        return fake

    return install


def test_the_newest_completed_job_for_this_site_is_chosen_not_the_runs_own_conclusion(github):
    # the newest run: welland passed, ps1 failed (the run itself is "failure"); an older run had welland failing
    fake = github({3: [job("plan", "success", run=3), job("shared (welland)", "success", run=3),
                       job("shared (ps1)", "failure", run=3)],
                   2: [job("shared (welland)", "failure", run=2)]})
    run = e2e.latest("welland")
    assert run["result"] == "passed" and run["url"].endswith("/runs/3/job/30")
    assert run["completed_at"] == datetime(2026, 10, 11, 5, 34, 28, tzinfo=timezone.utc)
    assert e2e.latest("ps1")["result"] == "failed"
    assert all(t == e2e.TIMEOUT for t in fake.timeouts)


def test_runs_that_did_not_test_the_site_are_skipped(github):
    # a scheduled run that was not due has only a skipped "shared"; another is still running; another was cancelled
    github({4: [job("plan", "success"), job("shared", "skipped")],
            3: [job("shared (welland)", None, status="in_progress")],
            2: [job("shared (welland)", "cancelled")],
            1: [job("shared (welland)", "failure", run=1)]})
    run = e2e.latest("welland")
    assert run["result"] == "failed" and "/runs/1/" in run["url"]


def test_no_completed_run_in_the_newest_few_is_an_error_with_the_reason(github):
    github({1: [job("shared", "skipped")]})
    with pytest.raises(e2e.E2EError, match="no completed e2e run for welland among the newest"):
        e2e.latest("welland")


@pytest.mark.parametrize("kw, reason", [({"status": 403}, "HTTP 403"), ({"jobs_status": 500}, "HTTP 500")])
def test_an_api_error_status_is_an_error_with_the_status(github, kw, reason):
    github({1: []} if "status" in kw else {1: [job("shared (welland)", "success")]}, **kw)
    with pytest.raises(e2e.E2EError, match=reason):
        e2e.latest("welland")


def test_an_unreachable_api_and_an_unexpected_answer_are_errors(monkeypatch):
    def timeout(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr("management.e2e.requests.get", timeout)
    with pytest.raises(e2e.E2EError, match=r"not reachable \(Timeout\)"):
        e2e.latest("welland")
    monkeypatch.setattr("management.e2e.requests.get", lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: {"unexpected": 1}))
    from django.core.cache import caches

    caches["poe"].clear()  # the shared cache holds the failure above
    with pytest.raises(e2e.E2EError, match="shape this page does not know"):
        e2e.latest("welland")


def test_an_answer_is_cached_ten_minutes_and_a_failure_one(github, monkeypatch):
    fake = github({1: [job("shared (welland)", "success")]})
    stored = {}
    from django.core.cache import caches

    real = caches["poe"]  # the SHARED cache (redis in production): every worker reads the one answer

    class Recording:
        get = staticmethod(real.get)
        add = staticmethod(real.add)
        delete = staticmethod(real.delete)

        @staticmethod
        def set(key, value, timeout=None):
            stored[key] = timeout
            real.set(key, value, timeout)

    monkeypatch.setattr(e2e, "caches", {"poe": Recording})
    e2e.latest("welland")
    asked = len(fake.calls)
    e2e.latest("welland")
    assert len(fake.calls) == asked  # the second read came from the cache
    assert stored["management-e2e-welland"] == 600
    github({})
    with pytest.raises(e2e.E2EError):
        e2e.latest("ps1")
    assert stored["management-e2e-ps1"] == 60
    assert real.get("management-e2e-welland-lock") is None  # the lock is released after a read


class Clock:
    """time.monotonic and time.sleep for e2e: sleeping moves the clock, and `on_sleep` runs at each sleep."""

    def __init__(self, monkeypatch, on_sleep=lambda: None):
        self.now, self.on_sleep = 1000.0, on_sleep
        monkeypatch.setattr(e2e.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(e2e.time, "sleep", self.sleep)

    def sleep(self, seconds):
        self.now += seconds
        self.on_sleep()


def test_while_another_request_reads_the_api_this_one_shows_the_last_answer_and_does_not_ask(github, monkeypatch):
    from django.core.cache import caches

    github({1: [job("shared (welland)", "failure")]})
    assert e2e.latest("welland")["result"] == "failed"  # read, and kept as the last answer
    caches["poe"].delete("management-e2e-welland")  # the 10 minutes are over
    fake = github({1: [job("shared (welland)", "success")]})
    caches["poe"].add("management-e2e-welland-lock", 1, 30)  # another request is reading
    Clock(monkeypatch)
    assert e2e.latest("welland")["result"] == "failed"
    assert fake.calls == []


def test_with_no_last_answer_it_waits_for_the_other_requests_answer(github, monkeypatch):
    from django.core.cache import caches

    fake = github({})
    caches["poe"].add("management-e2e-welland-lock", 1, 30)
    answer = {"run": {"result": "passed", "completed_at": "2026-10-11T05:34:28+00:00", "url": "u"}}
    clock = Clock(monkeypatch, on_sleep=lambda: clock.now > 1002 and caches["poe"].set("management-e2e-welland", answer))
    assert e2e.latest("welland")["result"] == "passed"
    assert fake.calls == [] and 1002 < clock.now < 1000 + e2e.BUDGET_SECONDS


def test_with_no_answer_within_the_budget_the_wait_is_an_error_saying_so(github, monkeypatch):
    from django.core.cache import caches

    github({})
    caches["poe"].add("management-e2e-welland-lock", 1, 30)
    clock = Clock(monkeypatch)
    with pytest.raises(e2e.E2EError, match=f"gave no answer within {e2e.BUDGET_SECONDS} s"):
        e2e.latest("welland")
    assert clock.now >= 1000 + e2e.BUDGET_SECONDS


def test_one_read_ends_within_the_budget_and_the_lock_outlives_it(github, monkeypatch):
    # every request takes 4 s: the runs list and three runs' jobs fit 15 s, the fifth request is not made
    clock = Clock(monkeypatch)
    fake = github({n: [job("shared", "skipped")] for n in range(10, 0, -1)})
    real_call = fake.__call__

    def slow(url, params=None, timeout=None, headers=None):
        assert timeout <= e2e.TIMEOUT and clock.now + timeout <= 1000 + e2e.BUDGET_SECONDS + 1e-9
        clock.now += 4
        return real_call(url, params, timeout, headers)

    monkeypatch.setattr("management.e2e.requests.get", slow)
    with pytest.raises(e2e.E2EError, match=f"within {e2e.BUDGET_SECONDS} s"):
        e2e.latest("welland")
    assert len(fake.calls) == 4
    assert e2e.LOCK_SECONDS > e2e.BUDGET_SECONDS


def test_the_site_name_is_a_setting_else_the_first_label_of_the_domain(settings):
    settings.MANAGEMENT_SITE_NAME = ""
    settings.DOMAIN_NAME = "ps1.fpgas.online"
    assert e2e.site_name() == "ps1"
    settings.MANAGEMENT_SITE_NAME = "welland"
    assert e2e.site_name() == "welland"
    settings.MANAGEMENT_SITE_NAME = "Not A Site"
    with pytest.raises(ImproperlyConfigured):
        e2e.site_name()


def test_a_request_that_wins_the_lock_after_another_stored_its_answer_does_not_read_again(github, monkeypatch):
    from django.core.cache import caches

    fake = github({1: [job("shared (welland)", "failure")]})
    answer = {"run": {"result": "passed", "completed_at": "2026-10-11T05:34:28+00:00", "url": "u"}}
    real_add = caches["poe"].add

    def add_after_the_other_stored(key, value, timeout=None):  # the other request finished just before this lock
        caches["poe"].set("management-e2e-welland", answer)
        return real_add(key, value, timeout)

    monkeypatch.setattr(caches["poe"], "add", add_after_the_other_stored)
    assert e2e.latest("welland")["result"] == "passed"
    assert fake.calls == []
