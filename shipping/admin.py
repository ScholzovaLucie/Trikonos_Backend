from django.contrib import admin

from .models import Carrier, Shipment, ShippingRate


@admin.register(Carrier)
class CarrierAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'supports_pickup_point', 'supports_home_delivery', 'position', 'is_active']
    list_editable = ['position', 'is_active']


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ['order', 'carrier', 'tracking_number', 'status', 'created_at']
    list_filter = ['carrier', 'status']
    search_fields = ['tracking_number']


@admin.register(ShippingRate)
class ShippingRateAdmin(admin.ModelAdmin):
    list_display = ['carrier', 'delivery_type', 'price', 'free_from']
    list_filter = ['carrier', 'delivery_type']
