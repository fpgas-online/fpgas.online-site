# pibfpgas - the FPGA board pages


from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from fleet.services import board_claims
from pibup.forms import UploadFileForm

from .models import Pi


def shown(pis):
    """The Pis to offer. With FPGAS_REQUIRE_VERIFIED, only those whose FPGA
    check passed this boot: the others still boot and take ssh, so someone
    can log in and see what is wrong, but users are not sent to them. A row
    is claimed by the newest Pi registered with its hostname (the port it is
    on), or by its serial unless that Pi is registered on another port."""
    if not settings.FPGAS_REQUIRE_VERIFIED:
        return list(pis)
    hosts, serials = board_claims()
    return [pi for pi in pis
            if pi.hostname in hosts
            or (pi.serial_no in serials and serials[pi.serial_no] in ("", pi.hostname))]

def home(request):

    pis = shown(Pi.objects.all())

    return render(request, "index.html",
            {
                'pis': pis,
                "domain_name": settings.DOMAIN_NAME,
                })


def one(request, pino, template='fpga.html'):

    # pino: Pi Number (the port on the network switch the Pi is plugged into.)
    # template: the template to render (used to hack in the tt board page.)

    pi = get_object_or_404(Pi, port=pino)
    if not shown([pi]):
        raise Http404("this board's FPGA check has not passed this boot")

    form = UploadFileForm()

    return render(request, template,
            {
                "pi": pi,
                "pino": pino,
                "pw": settings.PI_PW,
                "domain_name": settings.DOMAIN_NAME,
                "form": form,
                })


def tt(request):
    # the TT board page is port 21's; a row there with no FPGA board is not one
    if not Pi.objects.filter(port=21).exclude(fpga_board="").exists():
        raise Http404("no TT board on port 21")
    return one(request, 21, 'tt.html')
