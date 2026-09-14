"""The classic board page upload box.

``fpga.html`` includes ``upload.html``: a multipart form that POSTs to
``/pibup/upload?pino=<port>``. The view looks the board up by switch port,
opens an SFTP session to it as ``pi`` with the shared password from
``settings.PI_PW`` (base64, like everywhere else on the site) and writes the
file into ``~/Uploads`` on the Pi. paramiko is faked here: nothing in this
file touches the network.
"""

import base64
import io

import pibup.views
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from pibfpgas.models import Pi

PI_PASSWORD = "raspberry"


class _FakeSFTPFile(io.BytesIO):
    def __exit__(self, *exc):
        # keep the buffer readable after the ``with`` block closes it
        return False


class FakeSSHClient:
    """Stands in for paramiko.SSHClient; records connect() and SFTP writes."""

    connects = []
    files = {}

    def load_system_host_keys(self):
        pass

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, hostname, **kwargs):
        self.connects.append((hostname, kwargs))

    def open_sftp(self):
        return self

    def open(self, path, mode="r"):
        return self.files.setdefault(path, _FakeSFTPFile())

    def close(self):
        pass


@pytest.fixture
def fake_ssh(monkeypatch):
    FakeSSHClient.connects = []
    FakeSSHClient.files = {}
    monkeypatch.setattr(pibup.views.paramiko, "SSHClient", FakeSSHClient)
    return FakeSSHClient


@pytest.fixture
def c(settings):
    settings.PI_PW = base64.b64encode(PI_PASSWORD.encode()).decode()
    settings.DOMAIN_NAME = "welland.fpgas.online"
    return Client(HTTP_HOST="welland.fpgas.online")


@pytest.mark.django_db
def test_upload_lands_in_uploads_on_the_board(c, fake_ssh):
    Pi.objects.create(port=42, switch=2)
    bitstream = SimpleUploadedFile("blinky.bit", b"\x00\x09\x0f\xf0" * 100)

    r = c.post("/pibup/upload?pino=42", {"file": bitstream})

    assert r.status_code == 302
    assert r["Location"] == "success?pino=42"
    assert fake_ssh.connects == [("10.21.2.42", {"username": "pi", "password": PI_PASSWORD})]
    assert fake_ssh.files["Uploads/blinky.bit"].getvalue() == b"\x00\x09\x0f\xf0" * 100


@pytest.mark.django_db
def test_upload_to_a_port_with_no_board_is_404(c, fake_ssh):
    r = c.post("/pibup/upload?pino=99", {"file": SimpleUploadedFile("x.bit", b"x")})

    assert r.status_code == 404
    assert fake_ssh.connects == []


@pytest.mark.django_db
def test_get_renders_the_bare_form(c):
    r = c.get("/pibup/upload?pino=42")

    assert r.status_code == 200
    html = r.content.decode()
    assert 'action="/pibup/upload?pino=42"' in html
    assert 'name="file"' in html
