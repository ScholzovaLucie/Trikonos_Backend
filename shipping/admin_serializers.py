from rest_framework import serializers

from core.exceptions import DuplicateQuantityDiscountError

from .models import (
    SHIPPING_PRICE_DELIVERY_TYPE_CHOICES,
    Carrier,
    CodSurcharge,
    PacketaConfig,
    ShippingPrice,
    ShippingQuantityDiscount,
)


class AdminCarrierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Carrier
        fields = ['id', 'name', 'code', 'position', 'is_active']

    def validate_code(self, value):
        return value.strip().upper()


class AdminShippingPriceSerializer(serializers.ModelSerializer):
    carrier_id = serializers.PrimaryKeyRelatedField(source='carrier', queryset=Carrier.objects.all())
    carrier_name = serializers.CharField(source='carrier.name', read_only=True)
    delivery_type = serializers.ChoiceField(choices=SHIPPING_PRICE_DELIVERY_TYPE_CHOICES)

    class Meta:
        model = ShippingPrice
        fields = ['id', 'carrier_id', 'carrier_name', 'delivery_type', 'price', 'updated_at']
        read_only_fields = ['updated_at']


class AdminCodSurchargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodSurcharge
        fields = ['amount', 'updated_at']
        read_only_fields = ['updated_at']


class AdminPacketaConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = PacketaConfig
        fields = ['api_password', 'eshop', 'home_delivery_address_id', 'label_format', 'updated_at']
        read_only_fields = ['updated_at']


class AdminShippingQuantityDiscountSerializer(serializers.ModelSerializer):
    shipping_price_id = serializers.PrimaryKeyRelatedField(source='shipping_price', queryset=ShippingPrice.objects.all())

    class Meta:
        model = ShippingQuantityDiscount
        fields = ['id', 'shipping_price_id', 'min_quantity', 'discount_percent', 'position', 'is_active']
        # Disables DRF's auto-generated UniqueTogetherValidator (which would
        # report this as a generic 400) in favour of the 409 raised in
        # validate() below - a duplicate threshold is a conflict with
        # existing admin data, not a malformed request.
        validators = []

    def validate_discount_percent(self, value):
        if not (0 <= value <= 100):
            raise serializers.ValidationError('discount_percent must be between 0 and 100.')
        return value

    def validate(self, attrs):
        shipping_price = attrs.get('shipping_price', getattr(self.instance, 'shipping_price', None))
        min_quantity = attrs.get('min_quantity', getattr(self.instance, 'min_quantity', None))
        duplicate = ShippingQuantityDiscount.objects.filter(shipping_price=shipping_price, min_quantity=min_quantity)
        if self.instance is not None:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise DuplicateQuantityDiscountError()
        return attrs
