# pibfpgas - the FPGA board pages


from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from fleet.services import verified_hostnames, verified_serials
from pibup.forms import UploadFileForm

from .models import Pi


def shown(pis):
    """The Pis to offer. With FPGAS_REQUIRE_VERIFIED, only those whose FPGA
    check passed this boot: the others still boot and take ssh, so someone
    can log in and see what is wrong, but users are not sent to them. A row
    is a verified Pi's by its serial, or by its hostname (the port it is on),
    so a Pi on a port whose row has no serial yet still claims it."""
    if not settings.FPGAS_REQUIRE_VERIFIED:
        return list(pis)
    verified = verified_serials()
    hosts = verified_hostnames(verified)
    return [pi for pi in pis
            if (pi.serial_no and pi.serial_no in verified) or pi.hostname in hosts]


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
    return one(request, 21, 'tt.html')
