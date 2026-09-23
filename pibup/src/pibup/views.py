# pibup - pib upload form

import base64
import logging

import paramiko
from django.conf import settings
from django.core.exceptions import BadRequest
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from pibfpgas.models import Pi

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
    """The board never answered: powered off, still booting, or refusing us."""


class TransferFailed(UploadError):
    """The board answered, but the file did not get there in one piece."""


class SiteMisconfigured(UploadError):
    """This deployment has no usable board password, so no upload can work."""

    status = 503


def board_from_request(request):
    """The port number and the board named by ``?pino=``.

    A link with no ``pino``, or one carrying something that is not a switch
    port number, is a broken link rather than a broken server: 400. A port
    number with no board behind it is a 404, from get_object_or_404. Either
    way the visitor never meets Django's 500 page, which is what
    ``request.GET['pino']`` gave them.
    """

    pino = request.GET.get("pino")
    if pino is None or not (pino.isascii() and pino.isdigit()) or not 0 < int(pino) < 10000:
        log.warning("upload request with no usable pino: %s", request.get_full_path())
        raise BadRequest("this page needs ?pino=<switch port>, naming the board to upload to")

    try:
        return pino, get_object_or_404(Pi, port=pino)
    except Pi.MultipleObjectsReturned:
        # welland numbers its ports per switch, so a bare port number can name
        # two boards. Guessing would upload to whichever the database listed
        # first, which is worse than saying we cannot tell.
        log.warning("pino %s names a board on more than one switch", pino)
        raise BadRequest(f"port {pino} is on more than one switch, so this page cannot tell which board you mean")


# @csrf_exempt
def pibup(request):

    pino, pi = board_from_request(request)
    log.debug("upload page for pi%s (%s)", pino, pi.ip)

    error = None
    status = 200

    if request.method == "POST":
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                handle_uploaded_file(request.FILES["file"], pino, pi.ip)
            except UploadError as e:
                error, status = str(e), e.status
            else:
                return HttpResponseRedirect(f"success?pino={pino}")
    else:
        form = UploadFileForm()

    return render(request, "upload.html",
            {
                "pino": pino,
                "form": form,
                "error": error,
                },
            status=status,
            )

def handle_uploaded_file(f, pino, ip):

    # The shared pi password, base64 in settings the same way the board page
    # hands it to the wssh terminal. Without it paramiko only tries whatever
    # ssh keys the gunicorn user happens to have.
    try:
        password = base64.b64decode(settings.PI_PW).decode()
    except (AttributeError, ValueError) as e:
        # a deployment whose gunicorn never got PI_PW, or got something that
        # is not base64. No board is going to let us in, so do not try.
        log.exception("PI_PW is missing or not base64; no upload can work")
        raise SiteMisconfigured(
            "This site has no working password for the boards, so the upload was not attempted. "
            "That is our fault, not yours -- please report it."
        ) from e

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(ip, username='pi', password=password, timeout=CONNECT_TIMEOUT)
    except (paramiko.SSHException, OSError) as e:
        # refused, unroutable, timed out, half-booted sshd, password not yet
        # accepted: from here they are all "the board is not there".
        log.exception("pi%s (%s): ssh connect failed", pino, ip)
        raise BoardUnreachable(f"pi{pino} did not answer, so nothing was uploaded. {RETRY_HINT}") from e

    file_name=f.name
    total = f.size

    # what the visitor is owed on a failure: whether any of their bitstream
    # reached the board, and how much of it.
    opened = False
    written = 0

    try:
        sftp = client.open_sftp()
        with sftp.open(f"Uploads/{file_name}", "wb+") as destination:
            opened = True
            for chunk in f.chunks():
                destination.write(chunk)
                written += len(chunk)
    except (paramiko.SSHException, OSError) as e:
        log.exception("pi%s (%s): upload of %s failed after %d of %d bytes", pino, ip, file_name, written, total)
        if opened:
            detail = (f"the transfer stopped after {written} of {total} bytes, so the copy in "
                      f"Uploads/{file_name} on the board is incomplete and must not be loaded.")
        else:
            detail = f"Uploads/{file_name} could not be opened, so nothing was written to the board."
        raise TransferFailed(f"pi{pino} answered, but {detail} {RETRY_HINT}") from e
    finally:
        client.close()

    log.info("pi%s (%s): uploaded %s (%d bytes) to Uploads", pino, ip, file_name, written)

def success(request):
    pino, pi = board_from_request(request)
    log.debug("upload succeeded for pi%s (%s)", pino, pi.ip)
    return render(request, "success.html",
            {
                "pino": pino,
                })
