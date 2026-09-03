from django.contrib import admin

from .models import ShopSetting


@admin.register(ShopSetting)
class ShopSettingAdmin(admin.ModelAdmin):
    list_display = ['key', 'updated_at']
    search_fields = ['key']
