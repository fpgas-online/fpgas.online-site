class TTSiteHostMiddleware:
    """Replaced by Task 3; placeholder so settings import cleanly."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)
