# pibup - pib upload form

import base64

import paramiko
from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from pibfpgas.models import Pi

from .forms import UploadFileForm


# @csrf_exempt
def pibup(request):

    pino=request.GET['pino']
    print(f"{pino=}")

    if request.method == "POST":
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            handle_uploaded_file(request.FILES["file"], pino)
            return HttpResponseRedirect(f"success?pino={pino}")
    else:
        form = UploadFileForm()
    return render(request, "upload.html",
            {
                "pino": pino,
                "form": form,
                })

def handle_uploaded_file(f, pino):

    ip = get_object_or_404(Pi, port=pino).ip

    print(f"{ip=}")

    # The shared pi password, base64 in settings the same way the board page
    # hands it to the wssh terminal. Without it paramiko only tries whatever
    # ssh keys the gunicorn user happens to have.
    password = base64.b64decode(settings.PI_PW).decode()

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, username='pi', password=password)
    sftp = client.open_sftp()

    file_name=f.name

    with sftp.open(f"Uploads/{file_name}", "wb+") as destination:
        for chunk in f.chunks():
            destination.write(chunk)

    client.close()

def success(request):
    pino=request.GET['pino']
    print(f"{pino=}")
    return render(request, "success.html",
            {
                "pino": pino,
                })
