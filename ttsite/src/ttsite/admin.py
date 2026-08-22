from django.contrib import admin

from .models import Board


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("slug", "kind", "switch", "port", "shuttle", "title", "enabled", "sort_order")
    list_filter = ("kind", "enabled")
    search_fields = ("slug", "title", "shuttle")
