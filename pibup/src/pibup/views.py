# pibup - pib upload form

import base64
import logging

import paramiko
from django.conf import settings
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
    """Something the visitor can read, in place of a 500 page."""


class BoardUnreachable(UploadError):
    """The board never answered: powered off, still booting, or refusing us."""


class TransferFailed(UploadError):
    """The board answered, but the file did not get there in one piece."""


# @csrf_exempt
def pibup(request):

    pino=request.GET['pino']
    pi = get_object_or_404(Pi, port=pino)
    log.debug("upload page for pi%s (%s)", pino, pi.ip)

    error = None

    if request.method == "POST":
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                handle_uploaded_file(request.FILES["file"], pino, pi.ip)
            except UploadError as e:
                error = str(e)
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
            # the board is upstream of us, the way the switch is upstream of
            # the PoE views: when it will not talk, that is a 502, not an OK
            status=502 if error else 200,
            )

def handle_uploaded_file(f, pino, ip):

    # The shared pi password, base64 in settings the same way the board page
    # hands it to the wssh terminal. Without it paramiko only tries whatever
    # ssh keys the gunicorn user happens to have.
    password = base64.b64decode(settings.PI_PW).decode()

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
    pino=request.GET['pino']
    log.debug("upload succeeded for pi%s", pino)
    return render(request, "success.html",
            {
                "pino": pino,
                })
