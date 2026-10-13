from django.db import transaction
from django.db.models import BooleanField, Case, Count, Q, When
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from accounts.models import StaffRole
from accounts.permissions import IsStaffOrAdmin

from . import services
from .admin_serializers import (
    LOCKED_UPDATE_FIELDS,
    AdminOrderAddressSerializer,
    AdminOrderDetailSerializer,
    AdminOrderItemPriceSerializer,
    AdminOrderListSerializer,
    AdminOrderStatusSerializer,
    AdminOrderUpdateSerializer,
    AdminPaymentStatusSerializer,
    AdminPlacementUpdateSerializer,
    AdminResendEmailSerializer,
    AdminShipmentStatusSerializer,
    AdminShippingCostSerializer,
)
from .models import Order, OrderStatus, PaymentStatus, ShipmentStatus
from .serializers import OrderCreateSerializer

_ORDERING_MAP = {
    '-createdAt': '-created_at',
    'createdAt': 'created_at',
    'total': 'total',
    '-total': '-total',
    'orderNumber': 'order_number',
    '-orderNumber': '-order_number',
}


class AdminOrderViewSet(viewsets.ModelViewSet):
    """/admin/orders - ADMIN and STAFF both get full read/write; the few
    ADMIN-only actions (REFUNDED, cancel-in-production) are gated inside
    orders.services via the caller's role, not at the permission-class level,
    since most of this viewset's actions are shared between both roles.
    """
    permission_classes = [IsStaffOrAdmin]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    filter_backends = []  # every filter below is bespoke (comma lists, camelCase params)

    def get_queryset(self):
        queryset = (
            Order.objects.select_related('customer', 'carrier')
            .annotate(
                item_count=Count('items', distinct=True),
                has_custom=Case(When(items__is_custom=True, then=True), default=False, output_field=BooleanField()),
            )
            .distinct()
        )
        params = self.request.query_params

        status_param = params.get('status')
        if status_param:
            queryset = queryset.filter(status__in=[v.strip() for v in status_param.split(',') if v.strip()])

        for model_field, query_param in (
            ('payment_status', 'paymentStatus'),
            ('shipment_status', 'shipmentStatus'),
            ('payment_method', 'paymentMethod'),
            ('carrier_id', 'carrier'),
            ('customer_id', 'customer'),
        ):
            value = params.get(query_param)
            if value:
                queryset = queryset.filter(**{model_field: value})

        has_custom = params.get('hasCustom')
        if has_custom is not None:
            wants_custom = has_custom.lower() in ('1', 'true', 'yes')
            queryset = queryset.filter(items__is_custom=True) if wants_custom else queryset.exclude(items__is_custom=True)
            queryset = queryset.distinct()

        created_from = params.get('createdFrom')
        if created_from:
            queryset = queryset.filter(created_at__date__gte=created_from)
        created_to = params.get('createdTo')
        if created_to:
            queryset = queryset.filter(created_at__date__lte=created_to)

        search_term = params.get('q')
        if search_term:
            queryset = queryset.filter(
                Q(order_number__icontains=search_term)
                | Q(customer__email__icontains=search_term)
                | Q(customer__first_name__icontains=search_term)
                | Q(customer__last_name__icontains=search_term)
                | Q(tracking_number__icontains=search_term)
            )

        ordering = _ORDERING_MAP.get(params.get('ordering', '-createdAt'), '-created_at')
        return queryset.order_by(ordering)

    def get_serializer_class(self):
        if self.action == 'list':
            return AdminOrderListSerializer
        if self.action == 'create':
            return OrderCreateSerializer
        return AdminOrderDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action == 'create':
            context['is_admin'] = True
        return context

    def _is_admin_role(self):
        return self.request.user.role == StaffRole.ADMIN

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        detail = AdminOrderDetailSerializer(order, context=self.get_serializer_context())
        return Response(detail.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        order = self.get_object()
        serializer = AdminOrderUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if set(data.keys()) & LOCKED_UPDATE_FIELDS:
            services.ensure_order_editable(order)

        if 'billing' in data:
            billing = data['billing']
            order.billing_full_name = billing['full_name']
            order.billing_company_name = billing.get('company_name', '')
            order.billing_ico = billing.get('ico', '')
            order.billing_dic = billing.get('dic', '')
            order.billing_street = billing['street']
            order.billing_city = billing['city']
            order.billing_zip = billing['zip']
            order.billing_country = billing.get('country', 'CZ')
        if 'shipping' in data:
            shipping = data['shipping']
            order.shipping_full_name = shipping['full_name']
            order.shipping_company_name = shipping.get('company_name', '')
            order.shipping_street = shipping['street']
            order.shipping_city = shipping['city']
            order.shipping_zip = shipping['zip']
            order.shipping_country = shipping.get('country', 'CZ')
        if 'carrier' in data:
            order.carrier = data['carrier']
        if 'delivery_type' in data:
            order.delivery_type = data['delivery_type']
        if 'pickup_point_id' in data:
            order.pickup_point_id = data['pickup_point_id']
        if 'pickup_point_name' in data:
            order.pickup_point_name = data['pickup_point_name']
        if 'payment_method' in data:
            order.payment_method = data['payment_method']
        if 'note' in data:
            order.note = data['note']
        if 'tracking_number' in data:
            order.tracking_number = data['tracking_number']  # never locked

        order.save()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['patch'], url_path=r'items/(?P<item_id>[^/.]+)')
    def update_item(self, request, pk=None, item_id=None):
        order = self.get_object()
        item = order.items.filter(id=item_id).first()
        if item is None:
            raise NotFound('Položka nenalezena.')

        serializer = AdminOrderItemPriceSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        kwargs = {k: v for k, v in serializer.validated_data.items() if k in ('unit_price', 'quantity')}
        services.update_item_pricing(order, item, **kwargs)

        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['patch'], url_path=r'items/(?P<item_id>[^/.]+)/placements/(?P<placement_id>[^/.]+)')
    def update_placement(self, request, pk=None, item_id=None, placement_id=None):
        order = self.get_object()
        services.ensure_order_editable(order)
        item = order.items.filter(id=item_id).first()
        if item is None:
            raise NotFound('Položka nenalezena.')
        placement = item.design_placements.filter(id=placement_id).first()
        if placement is None:
            raise NotFound('Umístění nenalezeno.')

        serializer = AdminPlacementUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        print_area = data.get('print_area', placement.print_area)
        width = data.get('width', placement.width)
        height = data.get('height', placement.height)
        x = data.get('x', placement.x)
        y = data.get('y', placement.y)

        if print_area.product_type_id != item.product_type_id:
            raise services.BusinessError(
                'Tiskový blok patří k jinému typu oblečení, než má položka.',
                code='PRINT_AREA_PRODUCT_TYPE_MISMATCH',
            )
        services.validate_placement_bounds(print_area, x, y, width, height)

        for field in ('print_area', 'x', 'y', 'width', 'height', 'rotation_deg'):
            if field in data:
                setattr(placement, field, data[field])
        placement.save()

        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['get'], url_path=r'items/(?P<item_id>[^/.]+)/placements/(?P<placement_id>[^/.]+)/file')
    def placement_file(self, request, pk=None, item_id=None, placement_id=None):
        from django.http import HttpResponseRedirect

        order = self.get_object()
        item = order.items.filter(id=item_id).first()
        if item is None:
            raise NotFound('Položka nenalezena.')
        placement = item.design_placements.filter(id=placement_id).first()
        if placement is None:
            raise NotFound('Umístění nenalezeno.')
        # Local/dev storage serves files directly; swap for an attachment
        # proxy response if/when file storage moves behind a private bucket.
        return HttpResponseRedirect(placement.url)

    @action(detail=True, methods=['put'], url_path='shipping-cost')
    def shipping_cost(self, request, pk=None):
        order = self.get_object()
        serializer = AdminShippingCostSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.set_shipping_cost(order, serializer.validated_data['shipping_cost'])
        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        from .emails import send_order_confirmed_email

        order = self.get_object()
        message = request.data.get('message')
        with transaction.atomic():
            services.confirm_order(order)
            transaction.on_commit(lambda: send_order_confirmed_email(order, message=message))
        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'])
    def status(self, request, pk=None):
        from .emails import send_order_cancelled_email, send_order_shipped_email

        order = self.get_object()
        serializer = AdminOrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_status = serializer.validated_data['status']
        if target_status not in OrderStatus.values:
            raise serializers.ValidationError({'status': 'Neznámý stav objednávky.'})

        with transaction.atomic():
            services.transition_order_status(order, target_status, is_admin=self._is_admin_role())
            if order.status == OrderStatus.SHIPPED:
                transaction.on_commit(lambda: send_order_shipped_email(order))
            elif order.status == OrderStatus.CANCELLED:
                transaction.on_commit(lambda: send_order_cancelled_email(order))

        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['patch'], url_path='payment-status')
    def payment_status(self, request, pk=None):
        order = self.get_object()
        serializer = AdminPaymentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = serializer.validated_data['payment_status']
        if value not in PaymentStatus.values:
            raise serializers.ValidationError({'payment_status': 'Neznámý stav platby.'})
        order.payment_status = value
        order.save(update_fields=['payment_status', 'updated_at'])
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['patch'], url_path='shipment-status')
    def shipment_status(self, request, pk=None):
        order = self.get_object()
        serializer = AdminShipmentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = serializer.validated_data['shipment_status']
        if value not in ShipmentStatus.values:
            raise serializers.ValidationError({'shipment_status': 'Neznámý stav zásilky.'})
        order.shipment_status = value
        if 'tracking_number' in serializer.validated_data:
            order.tracking_number = serializer.validated_data['tracking_number']
        order.save()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'], url_path='resend-email')
    def resend_email(self, request, pk=None):
        order = self.get_object()
        serializer = AdminResendEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .emails import resend_order_email
        resend_order_email(order, serializer.validated_data['type'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'], url_path='print-sheet')
    def print_sheet(self, request, pk=None):
        order = self.get_object()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)
