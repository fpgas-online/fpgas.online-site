# pibup - pib upload form


from django.conf import settings
from django.shortcuts import get_object_or_404, render
from pibup.forms import UploadFileForm

from .models import Pi


def home(request):

    pis = Pi.objects.all()

    return render(request, "index.html",
            {
                'pis': pis,
                "domain_name": settings.DOMAIN_NAME,
                })


def one(request, pino, template='fpga.html'):

    # pino: Pi Number (the port on the network switch the Pi is plugged into.)
    # template: the template to render (used to hack in the tt board page.)

    pi = get_object_or_404(Pi, port=pino)

    o=100+int(pino)
    ip=f'10.21.0.{o}'

    form = UploadFileForm()

    return render(request, template,
            {
                "pi": pi,
                "pino": pino,
                "o": o,
                "ip": ip,
                "pw": settings.PI_PW,
                "domain_name": settings.DOMAIN_NAME,
                "form": form,
                })


def tt(request):
    return one(request, 21, 'tt.html')

