from django.contrib import admin

from .models import Board


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("slug", "usb_serial", "kind", "shuttle", "title", "sort_order")
    list_filter = ("kind",)
    search_fields = ("slug", "title", "shuttle", "usb_serial")
