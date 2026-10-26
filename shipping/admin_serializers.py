from rest_framework import serializers

from .models import Carrier, ShippingPrice, ShippingQuantityDiscount


class AdminCarrierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Carrier
        fields = ['id', 'name', 'code', 'position', 'is_active']

    def validate_code(self, value):
        return value.strip().upper()


class AdminShippingPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingPrice
        fields = ['price', 'updated_at']
        read_only_fields = ['updated_at']


class AdminShippingQuantityDiscountSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingQuantityDiscount
        fields = ['id', 'min_quantity', 'discount_percent', 'position', 'is_active']

    def validate_discount_percent(self, value):
        if not (0 <= value <= 100):
            raise serializers.ValidationError('discount_percent must be between 0 and 100.')
        return value
