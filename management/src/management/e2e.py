"""The newest browser test of this site: a run of the e2e workflow of fpgas-online/fpgas.online-e2e-tests.

That workflow (e2e.yml) runs for both sites at once: its `shared` job is a matrix over the site, so a run has a job
named "shared (welland)" and one named "shared (ps1)", each with its own result (the run's own conclusion is the worst
of the two, and a scheduled run that was not due has no matrix jobs at all, only a skipped "shared"). So the newest
completed run for this site is the newest run that has a completed "shared (<site>)" job with a result of success or
failure; its job is what the row reports and links to.

Read server-side from the public GitHub API with no token (the repository is public), cached in the default cache, with
a short timeout. Unauthenticated reads are limited per address, which is why the answer is cached for CACHE_SECONDS and
a failure for FAILURE_SECONDS, so that a down API is asked about once a minute, not once a page view."""

import re
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

API = "https://api.github.com"
REPO = "fpgas-online/fpgas.online-e2e-tests"
WORKFLOW = "e2e.yml"
WORKFLOW_URL = f"https://github.com/{REPO}/actions/workflows/{WORKFLOW}"
JOB = "shared ({site})"
RESULTS = {"success": "passed", "failure": "failed"}
RUNS_LOOKED_AT = 10
TIMEOUT = 5  # seconds, per request
CACHE_SECONDS = 600
FAILURE_SECONDS = 60
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


def _get(path, **params):
    try:
        response = requests.get(f"{API}{path}", params=params, timeout=TIMEOUT,
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
    runs = _get(f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs", per_page=RUNS_LOOKED_AT)
    try:
        for run in runs["workflow_runs"]:
            for job in _get(f"/repos/{REPO}/actions/runs/{run['id']}/jobs", per_page=100)["jobs"]:
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
    key = f"management-e2e-{site}"
    hit = cache.get(key)
    if hit is None:
        try:
            hit, seconds = {"run": _fetch(site)}, CACHE_SECONDS
        except E2EError as exc:
            hit, seconds = {"error": str(exc)}, FAILURE_SECONDS
        cache.set(key, hit, seconds)
    if "error" in hit:
        raise E2EError(hit["error"])
    return {**hit["run"], "completed_at": datetime.fromisoformat(hit["run"]["completed_at"])}
