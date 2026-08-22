"""Serve ttsite.urls on the tinytapeout.fpgas.online host; leave every other host alone."""

from django.conf import settings


class TTSiteHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":", 1)[0].lower()
        if host == settings.TTSITE_HOST.lower():
            request.urlconf = "ttsite.urls"
        return self.get_response(request)
