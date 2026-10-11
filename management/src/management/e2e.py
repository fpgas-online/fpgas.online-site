"""The newest browser test of this site: a run of the e2e workflow of fpgas-online/fpgas.online-e2e-tests.

That workflow (e2e.yml) runs for both sites at once: its `shared` job is a matrix over the site, so a run has a job
named "shared (welland)" and one named "shared (ps1)", each with its own result (the run's own conclusion is the worst
of the two, and a scheduled run that was not due has no matrix jobs at all, only a skipped "shared"). So the newest
completed run for this site is the newest run that has a completed "shared (<site>)" job with a result of success or
failure; its job is what the row reports and links to.

Read server-side from the public GitHub API with no token (the repository is public), with a short timeout.
Unauthenticated reads are limited to 60 an hour per address, so the answer goes in the SHARED cache (the redis
"poe" cache every worker reads; the default cache is per process, so each worker would ask on its own): an answer
for CACHE_SECONDS, a failure for FAILURE_SECONDS. One worker reads at a time (an add-lock); one read, all of its
requests, ends within BUDGET_SECONDS, and the lock outlives it. A request that finds the lock taken does not ask
too: it shows the last answer read (kept LAST_SECONDS), or, when there is none, waits for the reader's answer for
up to the budget."""

import re
import time
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured

API = "https://api.github.com"
REPO = "fpgas-online/fpgas.online-e2e-tests"
WORKFLOW = "e2e.yml"
WORKFLOW_URL = f"https://github.com/{REPO}/actions/workflows/{WORKFLOW}"
JOB = "shared ({site})"
RESULTS = {"success": "passed", "failure": "failed"}
RUNS_LOOKED_AT = 10
TIMEOUT = 5  # seconds, per request
BUDGET_SECONDS = 15  # one read, every request of it; a read that needs longer is a failure saying so
CACHE_SECONDS = 600
FAILURE_SECONDS = 60
LOCK_SECONDS = BUDGET_SECONDS + 5  # outlives the read it guards
LAST_SECONDS = 86400  # the last answer, shown while another request reads a new one
WAIT_STEP = 0.25  # seconds between looks at the cache while waiting for another request's read
# The workflow runs every 2 hours (its schedule); a newest completed run older than three of those is a failure:
# the test has stopped running, and an old pass says nothing about the site now.
STALE_AFTER = timedelta(hours=6)
SITE = re.compile(r"[a-z0-9][a-z0-9-]*")


class E2EError(Exception):
    """The newest run cannot be read or does not exist; the message says why."""


def site_name():
    """The site's name as the e2e workflow's matrix has it ("welland", "ps1"): MANAGEMENT_SITE_NAME when the site's
    settings name one, else the first label of DOMAIN_NAME (welland.fpgas.online). Neither: a mistake to say."""
    named = getattr(settings, "MANAGEMENT_SITE_NAME", "") or getattr(settings, "DOMAIN_NAME", "").split(".")[0]
    if not SITE.fullmatch(named):
        raise ImproperlyConfigured(
            f"the e2e site name {named!r} is not a site name: set MANAGEMENT_SITE_NAME (or DOMAIN_NAME)")
    return named


def _get(deadline, path, **params):
    left = deadline - time.monotonic()
    if left <= 0:
        raise E2EError(f"GitHub API did not answer every request within {BUDGET_SECONDS} s")
    try:
        response = requests.get(f"{API}{path}", params=params, timeout=min(TIMEOUT, left),
                                headers={"Accept": "application/vnd.github+json"})
    except requests.RequestException as exc:
        raise E2EError(f"GitHub API not reachable ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise E2EError(f"GitHub API answered HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise E2EError("GitHub API answered something that is not JSON") from exc


def _fetch(site):
    deadline = time.monotonic() + BUDGET_SECONDS
    runs = _get(deadline, f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs", per_page=RUNS_LOOKED_AT)
    try:
        for run in runs["workflow_runs"]:
            for job in _get(deadline, f"/repos/{REPO}/actions/runs/{run['id']}/jobs", per_page=100)["jobs"]:
                if job["name"] == JOB.format(site=site) and job["status"] == "completed" \
                        and job["conclusion"] in RESULTS:
                    return {"result": RESULTS[job["conclusion"]], "completed_at": job["completed_at"],
                            "url": job["html_url"]}
    except (KeyError, TypeError) as exc:
        raise E2EError(f"GitHub API answered a run list in a shape this page does not know ({exc!r})") from exc
    raise E2EError(f"no completed e2e run for {site} among the newest {RUNS_LOOKED_AT}")


def latest(site):
    """{"result": "passed" | "failed", "completed_at": datetime, "url"} of the newest completed run for `site`.
    Raises E2EError (the reason) when it cannot be told; the answer, or the reason, is cached."""
    cache, key = caches["poe"], f"management-e2e-{site}"
    hit = cache.get(key)
    if hit is None:
        hit = _read(cache, key, site) if cache.add(f"{key}-lock", 1, LOCK_SECONDS) else _wait(cache, key)
    if "error" in hit:
        raise E2EError(hit["error"])
    return {**hit["run"], "completed_at": datetime.fromisoformat(hit["run"]["completed_at"])}


def _read(cache, key, site):
    """Read the API as the lock's holder; store the answer (and, for a run, the last answer) for the others."""
    try:
        try:
            hit, seconds = {"run": _fetch(site)}, CACHE_SECONDS
            cache.set(f"{key}-last", hit, LAST_SECONDS)
        except E2EError as exc:
            hit, seconds = {"error": str(exc)}, FAILURE_SECONDS
        cache.set(key, hit, seconds)
        return hit
    finally:
        cache.delete(f"{key}-lock")


def _wait(cache, key):
    """Another request holds the lock: its last answer, else its new one when it comes, within the budget."""
    last = cache.get(f"{key}-last")
    if last is not None:
        return last
    deadline = time.monotonic() + BUDGET_SECONDS
    while time.monotonic() < deadline:
        time.sleep(WAIT_STEP)
        if (hit := cache.get(key)) is not None:
            return hit
    return {"error": f"another request's read of the GitHub API gave no answer within {BUDGET_SECONDS} s"}
