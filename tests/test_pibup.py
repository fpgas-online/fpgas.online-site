"""The classic board page upload box.

``fpga.html`` includes ``upload.html``: a multipart form that POSTs to
``/pibup/upload?pino=<port>``. The view looks the board up by switch port,
opens an SFTP session to it as ``pi`` with the shared password from
``settings.PI_PW`` (base64, like everywhere else on the site) and writes the
file into ``~/Uploads`` on the Pi. paramiko is faked here: nothing in this
file touches the network.

The failure tests are the ones production asked for. A board that has just
been PoE reset is gone for about two minutes, and every upload aimed at it in
that window used to end in Django's "Server Error (500)" page.
"""

import base64
import io
import logging
import socket

import paramiko
import pibup.views
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from pibfpgas.models import Pi

PI_PASSWORD = "raspberry"

# Four 64 KiB chunks, so f.chunks() hands the view more than one write and a
# transfer can plausibly die partway through.
BITSTREAM = b"\x00\x09\x0f\xf0" * 64 * 1024


class _FakeSFTPFile(io.BytesIO):
    """A file on the board. ``fail_after`` makes the write blow up partway."""

    fail_after = None

    def write(self, data):
        written = super().write(data)
        if self.fail_after is not None and self.tell() > self.fail_after:
            raise OSError("Failure")  # what paramiko raises for a dead channel
        return written

    def __exit__(self, *exc):
        # keep the buffer readable after the ``with`` block closes it
        return False


class FakeSSHClient:
    """Stands in for paramiko.SSHClient; records connect() and SFTP writes.

    The three class attributes let a test say how the board misbehaves:
    ``connect_error`` is raised out of connect(), ``open_error`` out of the
    SFTP open(), and ``fail_write_after`` kills the transfer once that many
    bytes have landed.
    """

    connects = []
    files = {}
    connect_error = None
    open_error = None
    fail_write_after = None
    closed = 0

    def load_system_host_keys(self):
        pass

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, hostname, **kwargs):
        self.connects.append((hostname, kwargs))
        if self.connect_error is not None:
            raise self.connect_error

    def open_sftp(self):
        return self

    def open(self, path, mode="r"):
        if self.open_error is not None:
            raise self.open_error
        f = self.files.setdefault(path, _FakeSFTPFile())
        f.fail_after = self.fail_write_after
        return f

    def close(self):
        type(self).closed += 1


@pytest.fixture
def fake_ssh(monkeypatch):
    FakeSSHClient.connects = []
    FakeSSHClient.files = {}
    FakeSSHClient.connect_error = None
    FakeSSHClient.open_error = None
    FakeSSHClient.fail_write_after = None
    FakeSSHClient.closed = 0
    monkeypatch.setattr(pibup.views.paramiko, "SSHClient", FakeSSHClient)
    return FakeSSHClient


@pytest.fixture
def c(settings):
    settings.PI_PW = base64.b64encode(PI_PASSWORD.encode()).decode()
    settings.DOMAIN_NAME = "welland.fpgas.online"
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.fixture
def board(db):
    return Pi.objects.create(port=42, switch=2)


def upload(c, pino=42, name="blinky.bit", content=BITSTREAM):
    return c.post(f"/pibup/upload?pino={pino}", {"file": SimpleUploadedFile(name, content)})


# -- the happy path ---------------------------------------------------------

def test_upload_lands_in_uploads_on_the_board(c, board, fake_ssh):
    r = upload(c)

    assert r.status_code == 302
    assert r["Location"] == "success?pino=42"
    assert fake_ssh.connects == [
        ("10.21.2.42", {"username": "pi", "password": PI_PASSWORD, "timeout": pibup.views.CONNECT_TIMEOUT})
    ]
    assert fake_ssh.files["Uploads/blinky.bit"].getvalue() == BITSTREAM


def test_connect_cannot_wait_forever_on_a_dead_board(c, board, fake_ssh):
    # a gunicorn worker is blocked for the whole of this; keep it short
    assert 0 < pibup.views.CONNECT_TIMEOUT <= 30


# -- the board is not answering ---------------------------------------------

UNREACHABLE = [
    pytest.param(
        paramiko.ssh_exception.NoValidConnectionsError(
            {("10.21.2.42", 22): ConnectionRefusedError(111, "Connection refused")}),
        id="connection-refused"),
    pytest.param(socket.timeout("timed out"), id="timeout"),
    pytest.param(OSError(113, "No route to host"), id="no-route-to-host"),
    pytest.param(paramiko.AuthenticationException("Authentication failed."), id="auth-failure"),
    pytest.param(paramiko.SSHException("Error reading SSH protocol banner"), id="half-booted-sshd"),
]


@pytest.mark.parametrize("boom", UNREACHABLE)
def test_a_board_that_does_not_answer_gets_a_readable_page(c, board, fake_ssh, boom):
    fake_ssh.connect_error = boom

    r = upload(c)

    assert r.status_code == 502
    html = r.content.decode()
    assert "pi42 did not answer" in html
    assert "nothing was uploaded" in html
    assert "two minutes" in html
    assert "Reset" in html
    assert "try again" in html
    # and the form is still there to try again with
    assert 'action="/pibup/upload?pino=42"' in html
    assert 'name="file"' in html


@pytest.mark.parametrize("boom", UNREACHABLE)
def test_a_board_that_does_not_answer_is_logged_with_its_traceback(c, board, fake_ssh, boom, caplog):
    fake_ssh.connect_error = boom

    with caplog.at_level(logging.ERROR, logger="pibup.views"):
        upload(c)

    record, = [r for r in caplog.records if r.name == "pibup.views"]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert "10.21.2.42" in record.getMessage()


# -- the transfer died partway ----------------------------------------------

def test_a_transfer_that_dies_partway_says_the_copy_is_incomplete(c, board, fake_ssh):
    fake_ssh.fail_write_after = 64 * 1024  # one chunk lands, then the board goes

    r = upload(c)

    assert r.status_code == 502
    html = r.content.decode()
    assert "pi42 answered" in html
    assert "incomplete" in html
    assert f"{64 * 1024} of {len(BITSTREAM)} bytes" in html
    assert "try again" in html
    # a partial file really is sitting on the board
    assert 0 < len(fake_ssh.files["Uploads/blinky.bit"].getvalue()) < len(BITSTREAM)


def test_a_missing_uploads_directory_says_nothing_landed(c, board, fake_ssh):
    fake_ssh.open_error = FileNotFoundError(2, "No such file")

    r = upload(c)

    assert r.status_code == 502
    html = r.content.decode()
    assert "pi42 answered" in html
    assert "Nothing was written" in html
    assert "incomplete" not in html


def test_a_failed_transfer_is_logged_with_its_traceback(c, board, fake_ssh, caplog):
    fake_ssh.fail_write_after = 64 * 1024

    with caplog.at_level(logging.ERROR, logger="pibup.views"):
        upload(c)

    record, = [r for r in caplog.records if r.name == "pibup.views"]
    assert record.exc_info is not None
    assert "blinky.bit" in record.getMessage()


def test_the_ssh_connection_is_closed_even_when_the_transfer_fails(c, board, fake_ssh):
    fake_ssh.fail_write_after = 64 * 1024

    upload(c)

    assert fake_ssh.closed == 1


# -- which board ------------------------------------------------------------

def test_upload_to_a_port_with_no_board_is_404(c, board, fake_ssh):
    r = upload(c, pino=99)

    assert r.status_code == 404
    assert fake_ssh.connects == []


def test_get_renders_the_bare_form(c, board):
    r = c.get("/pibup/upload?pino=42")

    assert r.status_code == 200
    html = r.content.decode()
    assert 'action="/pibup/upload?pino=42"' in html
    assert 'name="file"' in html
