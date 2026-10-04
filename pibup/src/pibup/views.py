# pibup - pib upload form

import base64
import logging
from urllib.parse import urlencode

import paramiko
from django.conf import settings
from django.core.exceptions import BadRequest
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from pibfpgas.pis import offered_pi

from .forms import UploadFileForm

log = logging.getLogger(__name__)

# A board that is up answers ssh in well under a second. A board that is off,
# rebooting or unplugged never will, and paramiko's default is to wait on the
# socket's own timeout -- minutes, with a gunicorn worker held the whole time.
CONNECT_TIMEOUT = 10

# Pressing Reset on the board page cuts PoE and restores it, so the board
# reboots; it is gone for about two minutes. That is the usual reason an
# upload finds nobody home, so say so rather than leaving the visitor to
# guess whether the site or the board is broken.
RETRY_HINT = (
    "A board takes about two minutes to come back after a Reset, "
    "so if you just reset this one, give it a moment and try again."
)


class UploadError(Exception):
    """Something the visitor can read, in place of a 500 page.

    ``status`` is what the board page answers with: the board is upstream of
    us the way the switch is upstream of the PoE views, which answer 502 when
    it will not talk and 503 when the service was never configured.
    """

    status = 502


class BoardUnreachable(UploadError):
    """The board never answered: powered off, unreachable, or still booting."""


class LoginRefused(UploadError):
    """The board answered and rejected the shared pi login."""


class TransferFailed(UploadError):
    """The board answered, but the file did not get there in one piece."""


class SiteMisconfigured(UploadError):
    """This deployment has no usable board password, so no upload can work."""

    status = 503


def board_from_request(request):
    """The Pi named by ``?host=``: the board's registered hostname
    (pi-sw2-p42, or pi9 at a flat site).

    A link with no ``host``, or an empty one, is a broken link rather than a
    broken server: 400. Any other name the board pages do not offer (nothing
    registered with that name, or it has not checked in and passed its FPGA
    check this boot) is a 404: the offered list is the one rule for what a
    board name is, so nothing here judges the name's shape. Either way the
    visitor never meets Django's 500 page, which is what
    ``request.GET['host']`` gave them.

    A hostname names its switch as well as its port, and only the newest
    machine registered on a hostname is offered, so a request never names two
    boards and there is nothing to guess at.
    """

    host = request.GET.get("host")
    if not host:
        log.warning("upload request with no host: %s", request.get_full_path())
        raise BadRequest("this page needs ?host=<Pi hostname>, naming the board to upload to")

    # only to a Pi the board pages offer (checked in, FPGA check passed)
    pi = offered_pi(host)
    if pi is None:
        raise Http404("no Pi of that name has checked in and passed its FPGA check this boot")
    return pi


# @csrf_exempt
def pibup(request):

    pi = board_from_request(request)
    log.debug("upload page for %s (%s)", pi.hostname, pi.ip)

    error = None
    status = 200

    if request.method == "POST":
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                handle_uploaded_file(request.FILES["file"], pi)
            except UploadError as e:
                error, status = str(e), e.status
            else:
                return HttpResponseRedirect("success?" + urlencode({"host": pi.hostname}))
    else:
        form = UploadFileForm()

    return render(request, "upload.html",
            {
                "host": pi.hostname,
                "form": form,
                "error": error,
                },
            status=status,
            )

def handle_uploaded_file(f, pi):

    host, ip = pi.hostname, pi.ip

    # The shared pi password, base64 in settings the same way the board page
    # hands it to the wssh terminal. Without it paramiko only tries whatever
    # ssh keys the gunicorn user happens to have.
    try:
        password = base64.b64decode(settings.PI_PW).decode()
    except (AttributeError, TypeError, ValueError) as e:
        # a deployment whose gunicorn never got PI_PW, or got None, or got
        # something that is not base64. No board is going to let us in, so
        # do not try.
        log.exception("PI_PW is missing or not base64; no upload can work")
        raise SiteMisconfigured(
            "This site has no working password for the boards, so the upload was not attempted. "
            "That is our fault, not yours -- please report it."
        ) from e

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    file_name=f.name
    total = f.size

    # what the visitor is owed on a failure: whether any of their bitstream
    # reached the board, and how much of it.
    opened = False
    written = 0

    # one finally for connect and transfer: a connect that fails partway
    # (rejected login, bad host key) leaves paramiko's transport thread and
    # socket alive until the client is closed.
    try:
        try:
            client.connect(ip, username='pi', password=password, timeout=CONNECT_TIMEOUT)
        except paramiko.AuthenticationException as e:
            # the board is up and its sshd turned the shared login down.
            # Waiting out a reboot will not change that, so no Reset hint.
            log.exception("%s (%s): ssh login refused", host, ip)
            raise LoginRefused(
                f"{host} answered, but refused our login, so nothing was uploaded. "
                "That is our fault, not yours -- please report it."
            ) from e
        except (paramiko.SSHException, OSError) as e:
            # refused, unroutable, timed out, half-booted sshd: from here they
            # are all "the board is not there".
            log.exception("%s (%s): ssh connect failed", host, ip)
            raise BoardUnreachable(f"{host} did not answer, so nothing was uploaded. {RETRY_HINT}") from e

        try:
            sftp = client.open_sftp()
            with sftp.open(f"Uploads/{file_name}", "wb+") as destination:
                opened = True
                for chunk in f.chunks():
                    destination.write(chunk)
                    written += len(chunk)
        except (paramiko.SSHException, OSError, EOFError) as e:
            # EOFError: what paramiko's SFTP raises when the connection drops
            log.exception("%s (%s): upload of %s failed after %d of %d bytes", host, ip, file_name, written, total)
            if opened:
                detail = (f"the transfer stopped after {written} of {total} bytes, so the copy in "
                          f"Uploads/{file_name} on the board is incomplete and must not be loaded.")
            else:
                detail = f"Uploads/{file_name} could not be opened, so nothing was written to the board."
            raise TransferFailed(f"{host} answered, but {detail} {RETRY_HINT}") from e
    finally:
        client.close()

    log.info("%s (%s): uploaded %s (%d bytes) to Uploads", host, ip, file_name, written)

def success(request):
    pi = board_from_request(request)
    log.debug("upload succeeded for %s (%s)", pi.hostname, pi.ip)
    return render(request, "success.html",
            {
                "host": pi.hostname,
                })
