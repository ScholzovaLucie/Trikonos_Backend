from django.contrib import admin

from .models import CustomDesignPlacement, Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'customer', 'status', 'payment_status', 'shipment_status', 'created_at']
    list_filter = ['status', 'payment_status', 'shipment_status']
    search_fields = ['order_number', 'customer__email']
    inlines = [OrderItemInline]


admin.site.register(CustomDesignPlacement)
