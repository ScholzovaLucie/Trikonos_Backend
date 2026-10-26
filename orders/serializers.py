from drf_spectacular.utils import extend_schema_field
from django.utils.translation import gettext as _
from rest_framework import serializers

from catalog.models import Color, Cut, Motif, PrintArea, ProductType
from core.tokens import resolve_file_token
from shipping.models import Carrier

from . import services
from .models import DeliveryType, OrderStatus, PaymentMethod, Size


class CustomerInputSerializer(serializers.Serializer):
    email = serializers.EmailField()
    phone = serializers.CharField(required=False, allow_blank=True, default='')
    first_name = serializers.CharField(required=False, allow_blank=True, default='')
    last_name = serializers.CharField(required=False, allow_blank=True, default='')
    company_name = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')
    marketing_consent = serializers.BooleanField(required=False, default=False)


class BillingInputSerializer(serializers.Serializer):
    full_name = serializers.CharField()
    company_name = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')
    ico = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')
    dic = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')
    street = serializers.CharField()
    city = serializers.CharField()
    zip = serializers.CharField()
    country = serializers.CharField(default='CZ')

    def validate(self, attrs):
        ico = (attrs.get('ico') or '').strip()
        if ico and (not ico.isdigit() or len(ico) != 8):
            raise serializers.ValidationError({'ico': _('The company ID (IČO) must have 8 digits.')})
        if ico and not (attrs.get('company_name') or '').strip():
            raise serializers.ValidationError({'company_name': _('A company with an IČO must have a name.')})
        if attrs.get('country') == 'CZ':
            zip_code = (attrs.get('zip') or '').replace(' ', '')
            if not (zip_code.isdigit() and len(zip_code) == 5):
                raise serializers.ValidationError({'zip': _('The postal code must have 5 digits.')})
        return attrs


class ShippingInputSerializer(serializers.Serializer):
    same_as_billing = serializers.BooleanField(default=False)
    full_name = serializers.CharField(required=False, allow_blank=True, default='')
    company_name = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')
    street = serializers.CharField(required=False, allow_blank=True, default='')
    city = serializers.CharField(required=False, allow_blank=True, default='')
    zip = serializers.CharField(required=False, allow_blank=True, default='')
    country = serializers.CharField(default='CZ')


class DeliveryInputSerializer(serializers.Serializer):
    carrier_id = serializers.PrimaryKeyRelatedField(
        source='carrier', queryset=Carrier.objects.filter(is_active=True),
    )
    delivery_type = serializers.ChoiceField(choices=DeliveryType.choices)
    pickup_point_id = serializers.CharField(required=False, allow_blank=True, default='')
    pickup_point_name = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        if attrs['delivery_type'] == DeliveryType.PICKUP_POINT:
            if not attrs.get('pickup_point_id') or not attrs.get('pickup_point_name'):
                raise serializers.ValidationError(
                    _('A pickup point requires both pickupPointId and pickupPointName.')
                )
        return attrs


class PlacementInputSerializer(serializers.Serializer):
    print_area_id = serializers.PrimaryKeyRelatedField(
        source='print_area', queryset=PrintArea.objects.filter(is_active=True),
    )
    file_token = serializers.CharField(write_only=True)
    x = serializers.IntegerField(default=0)
    y = serializers.IntegerField(default=0)
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    rotation_deg = serializers.IntegerField(default=0)

    def validate_file_token(self, value):
        return resolve_file_token(value)


class OrderItemInputSerializer(serializers.Serializer):
    is_custom = serializers.BooleanField(default=False)
    motif_id = serializers.PrimaryKeyRelatedField(
        source='motif', queryset=Motif.objects.all(), required=False, allow_null=True, default=None,
    )
    custom_text = serializers.CharField(required=False, allow_blank=True, default='')
    product_type_id = serializers.PrimaryKeyRelatedField(source='product_type', queryset=ProductType.objects.all())
    cut_id = serializers.PrimaryKeyRelatedField(source='cut', queryset=Cut.objects.all())
    color_id = serializers.PrimaryKeyRelatedField(source='color', queryset=Color.objects.all())
    size = serializers.ChoiceField(choices=Size.choices)
    quantity = serializers.IntegerField(min_value=1, max_value=services.MAX_ITEM_QUANTITY)
    placements = PlacementInputSerializer(many=True, required=False, default=list)
    # Only honoured for admin-created orders (OrderCreateSerializer strips
    # this for public checkout) - manual pricing of a known item upfront.
    unit_price = serializers.IntegerField(required=False, allow_null=True, default=None)

    @staticmethod
    def to_service_item(item):
        return {
            'is_custom': item.get('is_custom', False),
            'motif': item.get('motif'),
            'custom_text': item.get('custom_text', ''),
            'product_type': item['product_type'],
            'cut': item['cut'],
            'color': item['color'],
            'size': item['size'],
            'quantity': item['quantity'],
            'unit_price': item.get('unit_price'),
            'placements': [
                {
                    'print_area': placement['print_area'],
                    'url': placement['file_token']['url'],
                    'x': placement.get('x', 0),
                    'y': placement.get('y', 0),
                    'width': placement['width'],
                    'height': placement['height'],
                    'rotation_deg': placement.get('rotation_deg', 0),
                }
                for placement in item.get('placements', [])
            ],
        }


class CartValidateSerializer(serializers.Serializer):
    items = OrderItemInputSerializer(many=True)


class OrderCreateSerializer(serializers.Serializer):
    """Shared by the public checkout and the admin manual-order endpoint -
    pass context={'is_admin': True} to relax termsAccepted and allow
    client-supplied unitPrice/shippingCost (see API guide 5.3).
    """
    customer = CustomerInputSerializer()
    billing = BillingInputSerializer()
    shipping = ShippingInputSerializer()
    delivery = DeliveryInputSerializer()
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices)
    note = serializers.CharField(required=False, allow_blank=True, default='')
    terms_accepted = serializers.BooleanField(required=False, default=False)
    items = OrderItemInputSerializer(many=True)
    shipping_cost = serializers.IntegerField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        is_admin = self.context.get('is_admin', False)

        if not is_admin and not attrs.get('terms_accepted'):
            raise serializers.ValidationError({'terms_accepted': _('You must accept the terms and conditions.')})
        if not attrs['items']:
            raise serializers.ValidationError({'items': _('The order must have at least one item.')})

        billing = attrs['billing']
        shipping = attrs['shipping']
        if shipping.get('same_as_billing'):
            attrs['_shipping_resolved'] = {
                'shipping_full_name': billing['full_name'],
                'shipping_company_name': billing.get('company_name', ''),
                'shipping_street': billing['street'],
                'shipping_city': billing['city'],
                'shipping_zip': billing['zip'],
                'shipping_country': billing.get('country', 'CZ'),
            }
        else:
            required = ['full_name', 'street', 'city', 'zip']
            missing = [field for field in required if not shipping.get(field)]
            if missing:
                raise serializers.ValidationError({
                    'shipping': _('Missing required shipping address fields: %(fields)s.') % {'fields': ", ".join(missing)},
                })
            attrs['_shipping_resolved'] = {
                'shipping_full_name': shipping['full_name'],
                'shipping_company_name': shipping.get('company_name', ''),
                'shipping_street': shipping['street'],
                'shipping_city': shipping['city'],
                'shipping_zip': shipping['zip'],
                'shipping_country': shipping.get('country', 'CZ'),
            }

        if not is_admin:
            for item in attrs['items']:
                item['unit_price'] = None

        return attrs

    def create(self, validated_data):
        is_admin = self.context.get('is_admin', False)
        billing = validated_data['billing']

        billing_data = {
            'billing_full_name': billing['full_name'],
            'billing_company_name': billing.get('company_name', ''),
            'billing_ico': billing.get('ico', ''),
            'billing_dic': billing.get('dic', ''),
            'billing_street': billing['street'],
            'billing_city': billing['city'],
            'billing_zip': billing['zip'],
            'billing_country': billing.get('country', 'CZ'),
        }
        delivery = validated_data['delivery']
        delivery_data = {
            'carrier': delivery['carrier'],
            'delivery_type': delivery['delivery_type'],
            'pickup_point_id': delivery.get('pickup_point_id', ''),
            'pickup_point_name': delivery.get('pickup_point_name', ''),
        }
        items_data = [OrderItemInputSerializer.to_service_item(item) for item in validated_data['items']]

        order = services.create_order(
            customer_data=validated_data['customer'],
            billing_data=billing_data,
            shipping_data=validated_data['_shipping_resolved'],
            delivery_data=delivery_data,
            payment_method=validated_data['payment_method'],
            note=validated_data.get('note', ''),
            items_data=items_data,
        )

        shipping_cost = validated_data.get('shipping_cost')
        if is_admin and shipping_cost is not None:
            services.set_shipping_cost(order, shipping_cost)

        return order


class OrderCreateResponseItemSerializer(serializers.Serializer):
    item_label = serializers.CharField()
    quantity = serializers.IntegerField()
    unit_price = serializers.IntegerField(allow_null=True)


class OrderCreateResponseSerializer(serializers.Serializer):
    order_number = serializers.CharField()
    status = serializers.CharField()
    requires_quote = serializers.SerializerMethodField()
    access_token = serializers.SerializerMethodField()
    subtotal = serializers.IntegerField(allow_null=True)
    shipping_cost = serializers.IntegerField(allow_null=True)
    total = serializers.IntegerField(allow_null=True)
    currency = serializers.CharField()
    items = serializers.SerializerMethodField()

    def get_requires_quote(self, obj) -> bool:
        return obj.status == OrderStatus.QUOTE_REQUESTED

    def get_access_token(self, obj) -> str:
        return self.context['access_token']

    @extend_schema_field(OrderCreateResponseItemSerializer(many=True))
    def get_items(self, obj):
        return OrderCreateResponseItemSerializer(obj.items.all(), many=True).data


class OrderLookupRequestSerializer(serializers.Serializer):
    order_number = serializers.CharField()
    email = serializers.EmailField()


class OrderPlacementPreviewSerializer(serializers.Serializer):
    url = serializers.CharField()
    print_area_name = serializers.CharField(source='print_area.name')


class OrderCustomerItemSerializer(serializers.Serializer):
    item_label = serializers.CharField()
    quantity = serializers.IntegerField()
    unit_price = serializers.IntegerField(allow_null=True)
    custom_text = serializers.CharField()
    placements = serializers.SerializerMethodField()

    @extend_schema_field(OrderPlacementPreviewSerializer(many=True))
    def get_placements(self, obj):
        return OrderPlacementPreviewSerializer(obj.design_placements.all(), many=True).data


class OrderCustomerAddressSerializer(serializers.Serializer):
    full_name = serializers.CharField()
    company_name = serializers.CharField()
    street = serializers.CharField()
    city = serializers.CharField()
    zip = serializers.CharField()
    country = serializers.CharField()


class OrderPaymentInstructionsSerializer(serializers.Serializer):
    bank_account = serializers.CharField()
    amount = serializers.IntegerField()
    variable_symbol = serializers.CharField()


class OrderCustomerDetailSerializer(serializers.Serializer):
    """Shape returned by both POST /orders/lookup and
    GET /orders/access/{accessToken} - everything a customer is allowed to
    see about their own order, nothing an admin-only view would expose.
    """
    order_number = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField(source='get_status_display')
    items = serializers.SerializerMethodField()
    carrier_name = serializers.SerializerMethodField()
    delivery_type = serializers.CharField(allow_null=True)
    pickup_point_name = serializers.CharField()
    tracking_number = serializers.CharField()
    payment_method = serializers.CharField(allow_null=True)
    payment_status = serializers.CharField()
    billing = serializers.SerializerMethodField()
    shipping = serializers.SerializerMethodField()
    subtotal = serializers.IntegerField(allow_null=True)
    shipping_cost = serializers.IntegerField(allow_null=True)
    total = serializers.IntegerField(allow_null=True)
    currency = serializers.CharField()
    payment_instructions = serializers.SerializerMethodField()
    access_token = serializers.SerializerMethodField()

    @extend_schema_field(OrderCustomerItemSerializer(many=True))
    def get_items(self, obj):
        return OrderCustomerItemSerializer(obj.items.all(), many=True).data

    def get_carrier_name(self, obj) -> str | None:
        return obj.carrier.name if obj.carrier_id else None

    @extend_schema_field(OrderCustomerAddressSerializer)
    def get_billing(self, obj):
        return OrderCustomerAddressSerializer({
            'full_name': obj.billing_full_name,
            'company_name': obj.billing_company_name,
            'street': obj.billing_street,
            'city': obj.billing_city,
            'zip': obj.billing_zip,
            'country': obj.billing_country,
        }).data

    @extend_schema_field(OrderCustomerAddressSerializer)
    def get_shipping(self, obj):
        return OrderCustomerAddressSerializer({
            'full_name': obj.shipping_full_name,
            'company_name': obj.shipping_company_name,
            'street': obj.shipping_street,
            'city': obj.shipping_city,
            'zip': obj.shipping_zip,
            'country': obj.shipping_country,
        }).data

    @extend_schema_field(OrderPaymentInstructionsSerializer(allow_null=True))
    def get_payment_instructions(self, obj):
        if obj.payment_method != PaymentMethod.BANK_TRANSFER or obj.total is None:
            return None
        from django.conf import settings
        variable_symbol = ''.join(ch for ch in obj.order_number if ch.isdigit())
        return {
            'bank_account': settings.SHOP_CONFIG['BANK_ACCOUNT'],
            'amount': obj.total,
            'variable_symbol': variable_symbol,
        }

    def get_access_token(self, obj) -> str:
        return self.context.get('access_token')


class CartValidateLineResultSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    errors = serializers.ListField(child=serializers.CharField())
    item_label = serializers.CharField(allow_null=True)
    unit_price = serializers.IntegerField(allow_null=True)
    line_total = serializers.IntegerField(allow_null=True)


class CartValidateResponseSerializer(serializers.Serializer):
    items = CartValidateLineResultSerializer(many=True)
    subtotal = serializers.IntegerField(allow_null=True)
    requires_quote = serializers.BooleanField()
    shipping_cost = serializers.IntegerField(allow_null=True)
    total = serializers.IntegerField(allow_null=True)


class DesignUploadRequestSerializer(serializers.Serializer):
    file = serializers.FileField()


class DesignUploadResponseSerializer(serializers.Serializer):
    file_token = serializers.CharField()
    url = serializers.CharField()
    file_name = serializers.CharField()
    size = serializers.IntegerField()
    mime_type = serializers.CharField()
    width_px = serializers.IntegerField(allow_null=True)
    height_px = serializers.IntegerField(allow_null=True)
