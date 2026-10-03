# pibup - pib upload form

import base64
from urllib.parse import urlencode

import paramiko
from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from pibfpgas.pis import offered_pi

from .forms import UploadFileForm


# @csrf_exempt
def pibup(request):

    # host: the board's registered hostname (pi-sw2-p42, or pi9 at a flat site)
    host = request.GET['host']

    if request.method == "POST":
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            handle_uploaded_file(request.FILES["file"], host)
            return HttpResponseRedirect("success?" + urlencode({"host": host}))
    else:
        form = UploadFileForm()
    return render(request, "upload.html",
            {
                "host": host,
                "form": form,
                })

def handle_uploaded_file(f, host):

    # only to a Pi the board pages offer (checked in, FPGA check passed)
    pi = offered_pi(host)
    if pi is None:
        raise Http404("no Pi of that name has checked in and passed its FPGA check this boot")

    # The shared pi password, base64 in settings the same way the board page
    # hands it to the wssh terminal. Without it paramiko only tries whatever
    # ssh keys the gunicorn user happens to have.
    password = base64.b64decode(settings.PI_PW).decode()

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(pi.ip, username='pi', password=password)
    sftp = client.open_sftp()

    file_name=f.name

    with sftp.open(f"Uploads/{file_name}", "wb+") as destination:
        for chunk in f.chunks():
            destination.write(chunk)

    client.close()

def success(request):
    return render(request, "success.html",
            {
                "host": request.GET['host'],
                })
