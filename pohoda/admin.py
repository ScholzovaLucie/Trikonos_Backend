from django.contrib import admin

from .models import PohodaSyncLog


@admin.register(PohodaSyncLog)
class PohodaSyncLogAdmin(admin.ModelAdmin):
    list_display = ['order', 'direction', 'status', 'synced_at']
    list_filter = ['direction', 'status']
