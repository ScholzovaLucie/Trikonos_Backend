from django.contrib import admin

from .models import Address, Customer, TeamInquiry


class AddressInline(admin.TabularInline):
    model = Address
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['email', 'first_name', 'last_name', 'phone', 'created_at']
    search_fields = ['email', 'first_name', 'last_name', 'phone']
    inlines = [AddressInline]


@admin.register(TeamInquiry)
class TeamInquiryAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'company_name', 'status', 'created_at']
    list_filter = ['status']
    search_fields = ['name', 'email', 'company_name']
