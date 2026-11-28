"""ttsite.library: the VGA library archive client. The archive is a fake `SESSION.get`; the clock is `library.now`."""

import copy
import hashlib
import json
import logging
import socket
import threading
import time
from pathlib import Path

import pytest
import requests
from django.core.cache import cache

from ttsite import library

BASE = "https://data.wafer.space/big-storage/tinytapeout-vga-videos/"
INDEX_URL = BASE + "fpga-library.json"
FIXTURE = json.loads((Path(__file__).parent / "data" / "fpga-library.json").read_text())


def fixture_bin(row):
    """The fixture's bin for `row`: ff 00 00 ff 7e aa 99 7e, zeros, byte 8 = row (4096 bytes)."""
    data = bytearray(4096)
    data[:8] = bytes.fromhex("ff0000ff7eaa997e")
    data[8] = row
    return bytes(data)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeResp:
    def __init__(self, status=200, body=b"", headers=None, chunk=65536, on_chunk=None, url=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self._chunk = chunk
        self._on_chunk = on_chunk
        self.closed = False
        self.url = url

    def iter_content(self, chunk_size=1):
        step = min(self._chunk, chunk_size)
        for i in range(0, len(self._body), step):
            if self._on_chunk:
                self._on_chunk()
            yield self._body[i : i + step]

    def close(self):
        self.closed = True


class Archive:
    """A fake `SESSION.get`: answers each call with the next response, and records the calls."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.given = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        resp.url = resp.url or url
        self.given.append(resp)
        return resp


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(library, "now", c)
    return c


@pytest.fixture
def archive(monkeypatch, settings):
    settings.TTSITE_LIBRARY_BASE_URL = BASE

    def install(*responses):
        a = Archive(*responses)
        monkeypatch.setattr(library.SESSION, "get", a)
        return a

    return install


def body_of(doc):
    return json.dumps(doc).encode()


def ok(doc=None, etag='"a1"'):
    return FakeResp(200, body_of(FIXTURE if doc is None else doc), {"ETag": etag})


# --- normalise


def test_normalise_fixture_keeps_all_in_order_and_adds_base_url():
    index, dropped = library.normalise(copy.deepcopy(FIXTURE), BASE)
    assert dropped == []
    assert index["schema"] == 1 and index["generated"] == FIXTURE["generated"] and index["base_url"] == BASE
    assert set(index) == {"schema", "generated", "base_url", "designs"}
    assert [e["id"] for e in index["designs"]] == [e["id"] for e in FIXTURE["designs"]]
    # Ruling CH: synthesis_differs is optional for now; a missing one stays null (false would mean "checked, exact")
    assert index["designs"][0] == {**FIXTURE["designs"][0], "synthesis_differs": None}
    assert all(e["synthesis_differs"] is None for e in index["designs"])
    # Ruling BJ: every fixture entry carries pdk_latches, and it is kept
    assert all(e["pdk_latches"] is False for e in index["designs"])


def test_normalise_keeps_pdk_latches():
    doc = copy.deepcopy(FIXTURE)
    doc["designs"][0]["pdk_latches"] = True
    doc["designs"][1]["pdk_latches"] = False
    index, dropped = library.normalise(doc, BASE)
    assert dropped == []
    assert [e["pdk_latches"] for e in index["designs"]] == [True, False, False, False, False]


def test_normalise_ignores_unknown_entry_keys():
    doc = copy.deepcopy(FIXTURE)
    doc["designs"][0]["extra"] = "x"
    index, dropped = library.normalise(doc, BASE)
    assert dropped == [] and "extra" not in index["designs"][0]


def test_normalise_keeps_synthesis_differs():
    doc = copy.deepcopy(FIXTURE)
    doc["designs"][0]["synthesis_differs"] = True
    doc["designs"][1]["synthesis_differs"] = False
    doc["designs"][2]["synthesis_differs"] = None
    index, dropped = library.normalise(doc, BASE)
    assert dropped == []
    assert [e["synthesis_differs"] for e in index["designs"]] == [True, False, None, None, None]


@pytest.mark.parametrize("value", [1, 0, "true", "", [], {}])
def test_normalise_drops_entry_with_non_bool_synthesis_differs(value):
    doc = copy.deepcopy(FIXTURE)
    doc["designs"][0]["synthesis_differs"] = value
    index, dropped = library.normalise(doc, BASE)
    assert len(index["designs"]) == 4
    assert dropped == ["designs[0] synthesis_differs: must be true, false or null"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("clock_hz", True),
        ("clock_hz", 0),
        ("bin", "ttsky25b/tt_um_lukasdragoste_vga_patterns/other-33f69e60c080.bin"),
        ("bin", "../../etc/passwd"),
        ("poster", "https://evil.example/x.png"),
        ("poster", "ttsky25a/tt_um_vga_example/x.png"),  # another design's directory
        ("ui_in", 2),  # outside the mask
        ("ui_in", None),  # mask is 1
        ("id", "ttsky25b_something_else"),
        ("title", ""),
        ("docs_url", "http://insecure.example/"),
        ("bin_bytes", 262145),
        ("pinout", {"ui_in": [""] * 7, "uo_out": [""] * 8, "uio": [""] * 8}),
        ("build_status", "maybe"),
        ("pdk_latches", 1),
        ("pdk_latches", "true"),
        ("pdk_latches", None),
    ],
)
def test_normalise_drops_bad_entry_and_logs(key, value, caplog):
    doc = copy.deepcopy(FIXTURE)
    doc["designs"][0][key] = value
    with caplog.at_level(logging.WARNING, logger="ttsite.library"):
        index, dropped = library.normalise(doc, BASE)
    assert [e["id"] for e in index["designs"]] == [e["id"] for e in FIXTURE["designs"][1:]]
    assert len(dropped) == 1 and dropped[0].startswith("designs[0]") and key in dropped[0]
    assert dropped[0] in caplog.text
    if key == "pdk_latches":
        assert dropped == ["designs[0] pdk_latches: must be a bool"]


def test_normalise_drops_entry_missing_a_key():
    doc = copy.deepcopy(FIXTURE)
    del doc["designs"][2]["video_10s_webm"]
    index, dropped = library.normalise(doc, BASE)
    assert len(index["designs"]) == 4 and dropped == ["designs[2] video_10s_webm: missing"]


def test_normalise_drops_entry_missing_pdk_latches():
    """Ruling BJ, step c: the key is required, with no default."""
    doc = copy.deepcopy(FIXTURE)
    del doc["designs"][0]["pdk_latches"]
    index, dropped = library.normalise(doc, BASE)
    assert len(index["designs"]) == 4 and dropped == ["designs[0] pdk_latches: missing"]


def test_normalise_drops_both_duplicates():
    doc = copy.deepcopy(FIXTURE)
    doc["designs"].insert(1, copy.deepcopy(doc["designs"][0]))
    index, dropped = library.normalise(doc, BASE)
    assert [e["id"] for e in index["designs"]] == [e["id"] for e in FIXTURE["designs"][1:]]
    assert len(dropped) == 2 and all("duplicate id" in d for d in dropped)


def test_normalise_drops_noncontiguous_shuttle():
    doc = copy.deepcopy(FIXTURE)
    # a second ttsky25b design after the ttsky25a one: ttsky25b, ttsky25a, ttsky25b
    late = copy.deepcopy(FIXTURE["designs"][1])  # ttsky25a/tt_um_vga_example, moved to ttsky25b
    late.update(id="ttsky25b_vga_example", shuttle="ttsky25b",
                bin=f"ttsky25b/tt_um_vga_example/ttsky25b_vga_example-{late['bin_sha256'][:12]}.bin",
                poster=None, preview_gif=None, video_10s_webm=None)  # fmt: skip
    doc["designs"].insert(2, late)
    index, dropped = library.normalise(doc, BASE)
    assert [e["id"] for e in index["designs"]] == [e["id"] for e in FIXTURE["designs"]]
    assert len(dropped) == 1 and dropped[0].startswith("designs[2]") and "unbroken run" in dropped[0]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema=2),
        lambda d: d.update(schema=True),
        lambda d: d.pop("generated"),
        lambda d: d.update(generated="2026-11-26 03:00"),
        lambda d: d.update(tthfv_commit="xyz"),
        lambda d: d.pop("videos_repo_commit"),
        lambda d: d.update(designs={}),
        lambda d: d.update(designs=[d["designs"][0]] * 2001),
    ],
)
def test_normalise_refuses_bad_top_level(mutate):
    doc = copy.deepcopy(FIXTURE)
    mutate(doc)
    with pytest.raises(library.ArchiveError):
        library.normalise(doc, BASE)


@pytest.mark.parametrize("doc", [[], "x", None, 1])
def test_normalise_refuses_non_object(doc):
    with pytest.raises(library.ArchiveError):
        library.normalise(doc, BASE)


# --- index


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_index_refuses_nan(archive, clock, constant):
    text = json.dumps(FIXTURE).replace('"clock_hz": 25175000', f'"clock_hz": {constant}', 1)
    a = archive(FakeResp(200, text.encode(), {"ETag": '"a1"'}))
    with pytest.raises(library.ArchiveError):
        library.index(clock() + 50)
    assert a.given[0].closed


def test_index_refuses_bool_in_int_field(archive, clock):
    doc = copy.deepcopy(FIXTURE)
    doc["designs"][0]["ui_in_mask"] = True
    archive(ok(doc))
    body, _etag, _age = library.index(clock() + 50)
    assert [e["id"] for e in json.loads(body)["designs"]] == [e["id"] for e in FIXTURE["designs"][1:]]


def test_index_returns_body_weak_etag_and_no_age(archive, clock):
    archive(ok())
    body, etag, age = library.index(clock() + 50)
    assert json.loads(body)["base_url"] == BASE and age is None
    assert etag == 'W/"sha256-' + hashlib.sha256(body).hexdigest()[:32] + '"'


def test_index_is_fresh_for_300s(archive, clock):
    a = archive(ok(), ok())
    first = library.index(clock() + 50)
    clock.t += library.FRESH_SECONDS - 1
    assert library.index(clock() + 50) == first and len(a.calls) == 1
    clock.t += 2
    library.index(clock() + 50)
    assert len(a.calls) == 2


def test_index_sends_if_none_match_and_keeps_copy_on_304(archive, clock):
    a = archive(ok(etag='"v1"'), FakeResp(304))
    body, etag, _ = library.index(clock() + 50)
    assert "If-None-Match" not in (a.calls[0][1].get("headers") or {})
    clock.t += library.FRESH_SECONDS + 1
    assert library.index(clock() + 50) == (body, etag, None)
    assert a.calls[1][1]["headers"]["If-None-Match"] == '"v1"'
    assert all(r.closed for r in a.given)
    # the 304 renewed the copy: fresh again for another 300 s
    clock.t += library.FRESH_SECONDS - 1
    library.index(clock() + 50)
    assert len(a.calls) == 2


def test_index_keeps_pdk_latches_end_to_end(archive, clock):
    doc = copy.deepcopy(FIXTURE)
    doc["designs"][0]["pdk_latches"] = True
    archive(ok(doc))
    body, _, _ = library.index(clock() + 50)
    assert [e["pdk_latches"] for e in json.loads(body)["designs"]] == [True, False, False, False, False]


def test_index_key_names_the_normaliser_version():
    assert library.INDEX_KEY == f"ttsite:library:index:v{library.NORMALISE_VERSION}"
    # 2 kept pdk_latches with a default of false; 3 requires it, so no copy carries that default; 4 adds the
    # nullable synthesis_differs (Ruling CH), so a copy without it is a miss
    assert library.NORMALISE_VERSION == 4


@pytest.mark.parametrize("old_key", ["ttsite:library:index", "ttsite:library:index:v2"])
def test_index_copy_from_an_older_normaliser_is_fetched_in_full(archive, clock, old_key):
    """A body cached by older code (pdk_latches cut, or defaulted) is never brought back by a 304 (Ruling BJ)."""
    doc = copy.deepcopy(FIXTURE)
    doc["designs"][0]["pdk_latches"] = True
    old, _ = library.normalise(copy.deepcopy(FIXTURE), BASE)
    for e in old["designs"]:
        del e["pdk_latches"]
    old_body = json.dumps(old).encode()
    # what the old code kept: under its key, fetched long ago, with the archive's current ETag
    cache.set(old_key, ('"v1"', old_body, library._site_etag(old_body), clock() - 1000), timeout=None)
    try:
        a = archive(ok(doc, etag='"v1"'))
        body, _, _ = library.index(clock() + 50)
        assert "If-None-Match" not in (a.calls[0][1].get("headers") or {})
        assert json.loads(body)["designs"][0]["pdk_latches"] is True
        assert cache.get(library.INDEX_KEY)[1] == body
    finally:
        cache.delete(old_key)


def test_index_404_is_not_published_and_cached_60s(archive, clock):
    a = archive(FakeResp(404), FakeResp(404))
    with pytest.raises(library.NotPublished):
        library.index(clock() + 50)
    clock.t += library.NOT_PUBLISHED_SECONDS - 1
    with pytest.raises(library.NotPublished):
        library.index(clock() + 50)
    assert len(a.calls) == 1
    clock.t += 2
    with pytest.raises(library.NotPublished):
        library.index(clock() + 50)
    assert len(a.calls) == 2 and all(r.closed for r in a.given)
    assert cache.get(library.INDEX_KEY) is None


def test_index_refuses_redirect(archive, clock):
    a = archive(FakeResp(302, headers={"Location": "https://evil.example/fpga-library.json"}))
    with pytest.raises(library.ArchiveError) as exc:
        library.index(clock() + 50)
    assert exc.value.message == "archive redirected; refusing"
    assert a.calls[0][1]["allow_redirects"] is False and a.calls[0][1]["stream"] is True
    assert a.given[0].closed and len(a.calls) == 1


def test_index_caps_size(archive, clock):
    a = archive(FakeResp(200, b" " * (library.INDEX_MAX_BYTES + 1), {"ETag": '"big"'}))
    with pytest.raises(library.ArchiveError):
        library.index(clock() + 50)
    assert a.given[0].closed


def test_index_other_status_is_archive_error(archive, clock):
    archive(FakeResp(500))
    with pytest.raises(library.ArchiveError) as exc:
        library.index(clock() + 50)
    assert type(exc.value) is library.ArchiveError


def test_index_serves_stale_then_gives_up(archive, clock):
    a = archive(ok(), FakeResp(503), requests.ConnectionError("down"), FakeResp(500))
    body, etag, _ = library.index(clock() + 50)
    clock.t += library.FRESH_SECONDS + 5
    assert library.index(clock() + 50) == (body, etag, library.FRESH_SECONDS + 5)
    clock.t += 100
    assert library.index(clock() + 50) == (body, etag, library.FRESH_SECONDS + 105)
    clock.t = 1000.0 + library.STALE_SECONDS + 1
    with pytest.raises(library.ArchiveError):
        library.index(clock() + 50)
    assert len(a.calls) == 4


def test_index_bad_republish_keeps_last_good_copy(archive, clock):
    archive(ok(), ok({"schema": 2}))
    body, etag, _ = library.index(clock() + 50)
    clock.t += library.FRESH_SECONDS + 1
    assert library.index(clock() + 50) == (body, etag, library.FRESH_SECONDS + 1)


def test_trickling_archive_hits_deadline(archive, clock):
    def tick():
        clock.t += 0.2

    a = archive(FakeResp(200, body_of(FIXTURE), {"ETag": '"t"'}, chunk=1, on_chunk=tick))
    start = clock()
    with pytest.raises(library.ArchiveError) as exc:
        library.index(start + 2)
    assert exc.value.message == "archive unreachable"
    assert clock() - start <= 2.2 and a.given[0].closed


def test_index_read_timeout_follows_deadline(archive, clock):
    a = archive(ok())
    library.index(clock() + 7)
    assert a.calls[0][1]["timeout"] == (3.05, 7)
    cache.clear()
    a = archive(ok())
    library.index(clock() + 0.1)
    assert a.calls[0][1]["timeout"] == (3.05, 0.5)


def test_index_past_deadline_never_asks(archive, clock):
    a = archive(ok())
    with pytest.raises(library.ArchiveError) as exc:
        library.index(clock())
    assert exc.value.message == "archive unreachable" and a.calls == []


def test_session_ignores_env_and_closes_responses(archive, clock):
    assert isinstance(library.SESSION, requests.Session) and library.SESSION.trust_env is False
    a = archive(ok(), FakeResp(200, fixture_bin(1)))
    library.index(clock() + 50)
    library.fetch_bin(library.entry("ttsky25a_vga_example", clock() + 50), clock() + 50)
    assert len(a.given) == 2 and all(r.closed for r in a.given)


def test_entry_and_ids(archive, clock):
    a = archive(ok())
    assert library.entry("ttsky25a_vga_example", clock() + 50)["bin_bytes"] == 4096
    assert library.entry("no_such_design", clock() + 50) is None
    assert library.ids(clock() + 5) == {e["id"] for e in FIXTURE["designs"]}
    assert len(a.calls) == 1  # one fetch; the rest is the fresh copy


# --- fetch_bin


def entry_row(row):
    index, _ = library.normalise(copy.deepcopy(FIXTURE), BASE)
    return index["designs"][row]


def test_fetch_bin_checks_size_hash_preamble(archive, clock):
    e = entry_row(1)
    a = archive(FakeResp(200, fixture_bin(1)))
    assert library.fetch_bin(e, clock() + 50) == fixture_bin(1)
    assert a.calls[0][0] == BASE + e["bin"] and a.calls[0][1]["allow_redirects"] is False

    no_preamble = bytes(4096)
    for bad, entry in [
        (fixture_bin(1)[:-1], e),  # short
        (fixture_bin(2), e),  # another design's bytes
        (fixture_bin(1) + b"\0" * (262145 - 4096), e),  # over the cap
        (no_preamble, dict(e, bin_sha256=hashlib.sha256(no_preamble).hexdigest())),
    ]:
        a = archive(FakeResp(200, bad))
        with pytest.raises(library.ArchiveError) as exc:
            library.fetch_bin(entry, clock() + 50)
        assert exc.value.message == "archive file does not match the index"
        assert a.given[0].closed


def test_fetch_bin_404_message(archive, clock):
    a = archive(FakeResp(404))
    with pytest.raises(library.ArchiveError) as exc:
        library.fetch_bin(entry_row(0), clock() + 50)
    assert exc.value.message == "archive file missing: the archive is still syncing; try again in a few minutes"
    assert a.given[0].closed


@pytest.mark.parametrize("err", [requests.ConnectionError("x"), requests.Timeout("x")])
def test_fetch_bin_transport_error_is_unreachable(archive, clock, err):
    archive(err)
    with pytest.raises(library.ArchiveError) as exc:
        library.fetch_bin(entry_row(0), clock() + 50)
    assert exc.value.message == "archive unreachable"


def test_fetch_bin_refuses_redirect(archive, clock):
    archive(FakeResp(301, headers={"Location": "https://evil.example/x.bin"}))
    with pytest.raises(library.ArchiveError) as exc:
        library.fetch_bin(entry_row(0), clock() + 50)
    assert exc.value.message == "archive redirected; refusing"


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/x.bin",
        "//evil.example/tt05/tt_um_x/x.bin",
        "/etc/passwd",
        "tt05/tt_um_flappy_vga_cutout1/../../../x.bin",
        "tt05/tt_um_flappy_vga_cutout1/x@evil.example",
        "tt05/tt_um_flappy_vga_cutout1/x.bin?u=https://evil.example",
        "tt05/tt_um_flappy_vga_cutout1/%2e%2e/%2e%2e/x.bin",
        "tt05/tt_um_flappy_vga_cutout1/%2e%2e%2fx.bin",
        "tt05/tt_um_flappy_vga_cutout1/..%5cx.bin",
        "tt05\\tt_um_flappy_vga_cutout1\\x.bin",
        "tt05/tt_um_flappy_vga_cutout1/..\\..\\x.bin",
        "tt05/tt_um_flappy_vga_cutout1/x\\@evil.example",
        "tt05/tt_um_flappy_vga_cutout1/\u0078.bin\u2025",
        "tt05/tt_um_flappy_vga_cutout1/ｘ.bin",
        "tt05/tt_um_flappy_vga_cutout1/x\u2215..\u2215x.bin",
        "ttö5/tt_um_flappy_vga_cutout1/x.bin",
    ],
)
def test_fetch_bin_never_leaves_base_host(archive, clock, path):
    a = archive(FakeResp(200, fixture_bin(4)))
    with pytest.raises(library.ArchiveError):
        library.fetch_bin(dict(entry_row(4), bin=path), clock() + 50)
    assert a.calls == []


def test_fetch_bin_refuses_answer_from_another_host(archive, clock):
    archive(FakeResp(200, fixture_bin(4), url="https://evil.example/x.bin"))
    with pytest.raises(library.ArchiveError):
        library.fetch_bin(entry_row(4), clock() + 50)


def test_base_url_must_be_https_and_end_in_slash(settings, clock):
    for bad in ["http://data.wafer.space/x/", "https://data.wafer.space/x"]:
        settings.TTSITE_LIBRARY_BASE_URL = bad
        with pytest.raises(library.ArchiveError):
            library.index(clock() + 50)


def test_index_refuses_declared_oversize_before_reading(archive, clock):
    resp = FakeResp(200, b"{}", {"ETag": '"big"', "Content-Length": str(library.INDEX_MAX_BYTES + 1)})
    resp.iter_content = lambda chunk_size=1: pytest.fail("the body was read")
    archive(resp)
    with pytest.raises(library.ArchiveError):
        library.index(clock() + 50)
    assert resp.closed


def test_fetch_bin_refuses_declared_oversize_before_reading(archive, clock):
    resp = FakeResp(200, fixture_bin(0), {"Content-Length": str(library.BIN_MAX_BYTES + 1)})
    resp.iter_content = lambda chunk_size=1: pytest.fail("the body was read")
    archive(resp)
    with pytest.raises(library.ArchiveError) as exc:
        library.fetch_bin(entry_row(0), clock() + 50)
    assert exc.value.message == "archive file does not match the index" and resp.closed


def test_normalise_contiguity_counts_dropped_entries():
    """As the reference checker: ttsky25b, a bad ttsky25a, ttsky25b again drops the second ttsky25b run."""
    doc = copy.deepcopy(FIXTURE)
    late = copy.deepcopy(FIXTURE["designs"][1])
    late.update(id="ttsky25b_vga_example", shuttle="ttsky25b",
                bin=f"ttsky25b/tt_um_vga_example/ttsky25b_vga_example-{late['bin_sha256'][:12]}.bin",
                poster=None, preview_gif=None, video_10s_webm=None)  # fmt: skip
    doc["designs"][1]["title"] = ""  # the ttsky25a entry is bad, so it is dropped
    doc["designs"].insert(2, late)
    index, dropped = library.normalise(doc, BASE)
    assert "ttsky25b_vga_example" not in [e["id"] for e in index["designs"]]
    assert [d.split(" ")[0] for d in dropped] == ["designs[1]", "designs[2]"] and "unbroken run" in dropped[1]


def test_empty_base_url_is_library_disabled(settings, archive, clock):
    settings.TTSITE_LIBRARY_BASE_URL = ""
    a = archive(ok())
    assert not issubclass(library.LibraryDisabled, library.ArchiveError)
    with pytest.raises(library.LibraryDisabled):
        library.base_url()
    with pytest.raises(library.LibraryDisabled):
        library.index(clock() + 50)
    with pytest.raises(library.LibraryDisabled):
        library.fetch_bin(entry_row(0), clock() + 50)
    assert a.calls == []


# --- a real local socket: the deadline must stop a read that is blocked inside one iter_content chunk


@pytest.fixture
def trickler(monkeypatch):
    """A local HTTP server that sends `head` then `tail` one byte every 50 ms, for up to 4 s."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()
    monkeypatch.setattr(library, "base_url", lambda: f"http://127.0.0.1:{srv.getsockname()[1]}/")

    def start(head, tail):
        def serve():
            conn, _ = srv.accept()
            try:
                conn.recv(65536)
                conn.sendall(head)
                end = time.monotonic() + 4.0
                for byte in tail:
                    if stop.is_set() or time.monotonic() > end:
                        break
                    conn.sendall(bytes([byte]))
                    time.sleep(0.05)
            except OSError:
                pass  # the client shut the connection
            finally:
                conn.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        return thread

    yield start
    stop.set()
    srv.close()


@pytest.mark.parametrize(
    "head,tail",
    [
        (b"HTTP/1.1 200 OK\r\nContent-Length: 200000\r\n\r\n", b"x" * 200),  # the body trickles
        (b"HTTP/1.1 200 OK\r\n\r\n", b"x" * 200),  # no Content-Length, so a shut socket looks like an end
        (b"", b"HTTP/1.1 200 OK\r\nX-Slow: " + b"y" * 200),  # the headers trickle
    ],
    ids=["body", "body-no-length", "headers"],
)
def test_real_trickling_archive_stops_at_deadline(trickler, head, tail):
    thread = trickler(head, tail)
    start = time.monotonic()
    with pytest.raises(library.ArchiveError) as exc:
        library.index(start + 1.0)
    took = time.monotonic() - start
    assert exc.value.message == "archive unreachable"
    assert 0.9 <= took < 1.5, f"the load took {took:.2f} s for a 1 s deadline"
    thread.join(2)


# --- hardening from the Task 6 re-review (N1-N3)


@pytest.fixture
def recorder(monkeypatch):
    """A local HTTP server that answers one request with the fixture index and keeps the request's bytes."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    monkeypatch.setattr(library, "base_url", lambda: f"http://127.0.0.1:{srv.getsockname()[1]}/")
    seen = []

    def serve():
        conn, _ = srv.accept()
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(65536)
            seen.append(request)
            body = body_of(FIXTURE)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield seen
    thread.join(2)
    srv.close()


def test_archive_requests_send_connection_close(recorder):
    """N1: no archive connection goes back to the pool, so a late watchdog never shuts a pooled socket."""
    library.index(time.monotonic() + 5)
    head = recorder[0].decode("latin-1").lower().split("\r\n")
    assert "connection: close" in head


def test_archive_session_mounts_the_watched_https_pool():
    """N2: the watchdog's hook relies on urllib3 2.x pool internals."""
    pool = library.SESSION.get_adapter(BASE).poolmanager.connection_from_url(BASE)
    assert type(pool) is library._HTTPSPool
    assert pool.ConnectionCls is library._HTTPSConnection


def test_urllib3_is_pinned_to_major_2():
    import tomllib

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert "urllib3>=2,<3" in pyproject["project"]["dependencies"]


@pytest.fixture
def stalled_tls(monkeypatch):
    """A local server that accepts the TCP connection and never answers the TLS ClientHello."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    monkeypatch.setattr(library, "base_url", lambda: f"https://127.0.0.1:{srv.getsockname()[1]}/")
    stop = threading.Event()
    held = []

    def serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        held.append(conn)
        stop.wait(5)
        conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(2)
    srv.close()


def test_real_stalled_tls_handshake_stops_at_deadline(stalled_tls):
    """N3: the raw socket is handed to the watchdog right after the TCP connect, so the handshake is covered (it
    runs under the 3.05 s connect timeout, not the deadline)."""
    start = time.monotonic()
    with pytest.raises(library.ArchiveError) as exc:
        library.index(start + 1.0)
    took = time.monotonic() - start
    assert exc.value.message == "archive unreachable"
    assert 0.9 <= took < 1.5, f"the handshake took {took:.2f} s for a 1 s deadline"


def test_watchdog_attaches_a_tls_socket_without_leaking():
    """An SSLSocket refuses socket.socket.dup (TypeError, after the descriptor is already dup'd); the watchdog must
    keep a plain duplicate, shut the connection through it, and close it again."""
    import os
    import ssl

    def fds():
        return len(os.listdir("/proc/self/fd"))

    a, b = socket.socketpair()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    tls = ctx.wrap_socket(a, do_handshake_on_connect=False)
    try:
        assert isinstance(tls, ssl.SSLSocket)
        before = fds()
        watchdog = library._Watchdog(60)
        watchdog.attach(tls)
        assert fds() == before + 1  # the duplicate it keeps
        watchdog._fire()  # the timer, without the wait
        b.settimeout(2)
        assert b.recv(1) == b""  # the connection was shut: the peer reads the end
        watchdog.close()
        assert fds() == before
        watchdog.attach(tls)  # after close() nothing more is kept
        assert fds() == before
    finally:
        tls.close()
        b.close()
