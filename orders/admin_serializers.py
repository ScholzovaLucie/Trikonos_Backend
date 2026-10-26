from decimal import Decimal

from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import StaffRole
from catalog.models import Color, Cut, Motif, PrintArea, ProductType
from customers.models import Customer
from shipping.models import Carrier

from . import services
from .models import CustomDesignPlacement, DeliveryType, Order, OrderItem, PaymentMethod


class CustomerRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ['id', 'email', 'phone', 'first_name', 'last_name', 'company_name']


class CarrierRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Carrier
        fields = ['id', 'name', 'code']


class MotifRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Motif
        fields = ['id', 'name', 'slug']


class ProductTypeRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductType
        fields = ['id', 'name']


class CutRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cut
        fields = ['id', 'name']


class ColorRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Color
        fields = ['id', 'name', 'hex_value']


class PrintAreaRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintArea
        fields = ['id', 'name', 'width_mm', 'height_mm']


class AdminPlacementSerializer(serializers.ModelSerializer):
    print_area = PrintAreaRefSerializer(read_only=True)

    class Meta:
        model = CustomDesignPlacement
        fields = ['id', 'print_area', 'url', 'x', 'y', 'width', 'height', 'rotation_deg']


class AdminOrderItemSerializer(serializers.ModelSerializer):
    motif = MotifRefSerializer(read_only=True)
    product_type = ProductTypeRefSerializer(read_only=True)
    cut = CutRefSerializer(read_only=True)
    color = ColorRefSerializer(read_only=True)
    placements = AdminPlacementSerializer(source='design_placements', many=True, read_only=True)

    class Meta:
        model = OrderItem
        fields = [
            'id', 'is_custom', 'motif', 'custom_text', 'product_type', 'cut', 'color',
            'size', 'item_label', 'unit_price', 'quantity', 'placements',
        ]


class AdminOrderAddressSerializer(serializers.Serializer):
    full_name = serializers.CharField()
    company_name = serializers.CharField(required=False, allow_blank=True, default='')
    ico = serializers.CharField(required=False, allow_blank=True, default='')
    dic = serializers.CharField(required=False, allow_blank=True, default='')
    street = serializers.CharField()
    city = serializers.CharField()
    zip = serializers.CharField()
    country = serializers.CharField(default='CZ')


class AdminOrderListSerializer(serializers.ModelSerializer):
    customer_email = serializers.CharField(source='customer.email', read_only=True)
    customer_name = serializers.SerializerMethodField()
    item_count = serializers.IntegerField(read_only=True)
    has_custom = serializers.BooleanField(read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'customer_email', 'customer_name', 'created_at',
            'item_count', 'total', 'currency', 'status', 'payment_status', 'shipment_status', 'has_custom',
        ]

    def get_customer_name(self, obj) -> str:
        return f'{obj.customer.first_name} {obj.customer.last_name}'.strip()


class AdminOrderDetailSerializer(serializers.ModelSerializer):
    customer = CustomerRefSerializer(read_only=True)
    carrier = CarrierRefSerializer(read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    items = AdminOrderItemSerializer(many=True, read_only=True)
    billing = serializers.SerializerMethodField()
    shipping = serializers.SerializerMethodField()
    allowed_transitions = serializers.SerializerMethodField()
    is_locked = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'customer', 'status', 'status_label',
            'subtotal', 'shipping_cost', 'shipping_cost_manual', 'total', 'currency',
            'carrier', 'delivery_type', 'pickup_point_id', 'pickup_point_name',
            'tracking_number', 'carrier_shipment_id', 'shipment_status',
            'payment_method', 'payment_status',
            'billing', 'shipping', 'note', 'items',
            'allowed_transitions', 'is_locked',
            'confirmed_at', 'created_at', 'updated_at',
        ]

    @extend_schema_field(AdminOrderAddressSerializer)
    def get_billing(self, obj):
        return AdminOrderAddressSerializer({
            'full_name': obj.billing_full_name, 'company_name': obj.billing_company_name,
            'ico': obj.billing_ico, 'dic': obj.billing_dic, 'street': obj.billing_street,
            'city': obj.billing_city, 'zip': obj.billing_zip, 'country': obj.billing_country,
        }).data

    @extend_schema_field(AdminOrderAddressSerializer)
    def get_shipping(self, obj):
        return AdminOrderAddressSerializer({
            'full_name': obj.shipping_full_name, 'company_name': obj.shipping_company_name,
            'ico': '', 'dic': '', 'street': obj.shipping_street,
            'city': obj.shipping_city, 'zip': obj.shipping_zip, 'country': obj.shipping_country,
        }).data

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_allowed_transitions(self, obj):
        request = self.context.get('request')
        is_admin_role = bool(request and request.user.role == StaffRole.ADMIN)
        return services.get_allowed_transitions(obj, is_admin=is_admin_role)

    def get_is_locked(self, obj) -> bool:
        return obj.status not in services.EDITABLE_STATUSES


class AdminOrderUpdateSerializer(serializers.Serializer):
    """PATCH /orders/{id} - addresses/carrier/delivery/payment method/note.
    trackingNumber is handled separately by the view since it may change in
    any status; everything else here is locked outside QUOTE_REQUESTED/NEW.
    """
    billing = AdminOrderAddressSerializer(required=False)
    shipping = AdminOrderAddressSerializer(required=False)
    carrier_id = serializers.PrimaryKeyRelatedField(
        source='carrier', queryset=Carrier.objects.all(), required=False, allow_null=True,
    )
    delivery_type = serializers.ChoiceField(choices=DeliveryType.choices, required=False, allow_null=True)
    pickup_point_id = serializers.CharField(required=False, allow_blank=True)
    pickup_point_name = serializers.CharField(required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices, required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True)
    tracking_number = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        # Partial update - only cross-check carrier/delivery_type against
        # the resulting state (this PATCH's value if present, else the
        # order's current value) when either one is actually being changed.
        if 'delivery_type' not in attrs and 'carrier' not in attrs:
            return attrs

        delivery_type = attrs['delivery_type'] if 'delivery_type' in attrs else (
            self.instance.delivery_type if self.instance else None
        )
        carrier = attrs['carrier'] if 'carrier' in attrs else (self.instance.carrier if self.instance else None)

        if delivery_type == DeliveryType.PERSONAL_PICKUP:
            if carrier is not None:
                raise serializers.ValidationError(_('Personal pickup must not have a carrier.'))
        elif delivery_type:
            if carrier is None:
                raise serializers.ValidationError(_('A carrier is required for this delivery type.'))
            if delivery_type == DeliveryType.PICKUP_POINT:
                pickup_point_id = attrs.get(
                    'pickup_point_id', self.instance.pickup_point_id if self.instance else '',
                )
                pickup_point_name = attrs.get(
                    'pickup_point_name', self.instance.pickup_point_name if self.instance else '',
                )
                if not pickup_point_id or not pickup_point_name:
                    raise serializers.ValidationError(
                        _('A pickup point requires both pickupPointId and pickupPointName.'),
                    )
        return attrs


LOCKED_UPDATE_FIELDS = {
    'billing', 'shipping', 'carrier_id', 'delivery_type',
    'pickup_point_id', 'pickup_point_name', 'payment_method', 'note',
}


class AdminOrderItemPriceSerializer(serializers.Serializer):
    unit_price = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    quantity = serializers.IntegerField(required=False, min_value=1, max_value=services.MAX_ITEM_QUANTITY)


class AdminShippingCostSerializer(serializers.Serializer):
    shipping_cost = serializers.IntegerField(min_value=0)


class AdminConfirmOrderSerializer(serializers.Serializer):
    message = serializers.CharField(required=False, allow_blank=True)


class AdminOrderStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    note = serializers.CharField(required=False, allow_blank=True)


class AdminPaymentStatusSerializer(serializers.Serializer):
    payment_status = serializers.CharField()


class AdminCreateShipmentSerializer(serializers.Serializer):
    """POST /orders/{id}/shipment. `weight` isn't tracked anywhere on the
    order/items (no product weighs anything in this system) - admin weighs
    the package and enters it here. `value` overrides the insurance value
    sent to Packeta (defaults to the order's total) in case admin wants to
    declare something else.
    """
    weight = serializers.DecimalField(max_digits=6, decimal_places=3, min_value=Decimal('0.001'))
    value = serializers.IntegerField(required=False, min_value=0)


class AdminShipmentStatusSerializer(serializers.Serializer):
    shipment_status = serializers.CharField()
    tracking_number = serializers.CharField(required=False, allow_blank=True)


class AdminResendEmailSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=[
        'ORDER_RECEIVED', 'ORDER_CONFIRMED', 'ORDER_SHIPPED', 'ORDER_CANCELLED',
    ])


class AdminPlacementUpdateSerializer(serializers.Serializer):
    print_area_id = serializers.PrimaryKeyRelatedField(source='print_area', queryset=PrintArea.objects.all(), required=False)
    x = serializers.IntegerField(required=False)
    y = serializers.IntegerField(required=False)
    width = serializers.IntegerField(required=False, min_value=1)
    height = serializers.IntegerField(required=False, min_value=1)
    rotation_deg = serializers.IntegerField(required=False)
