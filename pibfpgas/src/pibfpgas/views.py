# pibfpgas - the FPGA board pages


from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from pibup.forms import UploadFileForm

from .pis import listed, listed_pi


def home(request):

    return render(request, "index.html",
            {
                'pis': listed(),
                "domain_name": settings.DOMAIN_NAME,
                })


def render_pi(request, pi, template):
    return render(request, template,
            {
                "pi": pi,
                "pw": settings.PI_PW,
                "domain_name": settings.DOMAIN_NAME,
                "form": UploadFileForm(),
                })


def one(request, hostname):
    # hostname: the Pi's registered hostname (pi-sw2-p46, or pi9 at a flat site)
    pi = listed_pi(hostname)
    if pi is None:
        raise Http404("no Pi of that name has checked in and passed its FPGA check this boot")
    return render_pi(request, pi, 'fpga.html')


def tt(request):
    # the TT board page is port 21's: only when the Pi there found a TT board
    pi = next((pi for pi in listed()
               if pi.port == 21 and any(board["board"] == "tt" for board in pi.found)), None)
    if pi is None:
        raise Http404("no TT board found on port 21 this boot")
    return render_pi(request, pi, 'tt.html')
