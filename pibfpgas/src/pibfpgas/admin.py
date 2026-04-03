from django.contrib import admin

from .models import *  # noqa: F403


class PiAdmin(admin.ModelAdmin):
    pass

admin.site.register(Pi, PiAdmin)  # noqa: F405
