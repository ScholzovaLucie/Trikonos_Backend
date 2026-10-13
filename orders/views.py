from django.core import signing
from rest_framework import generics, serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import FileTooLargeError, UnsupportedFileTypeError
from core.tokens import resolve_order_access_token, sign_file_token, sign_order_access_token
from core.uploads import (
    JPG,
    MIME_BY_KIND,
    PDF,
    PNG,
    SVG,
    design_upload_directory,
    detect_kind,
    get_image_dimensions,
    sanitize_svg,
    save_random_file,
)

from . import services
from .models import Order
from .serializers import (
    CartValidateSerializer,
    OrderCreateResponseSerializer,
    OrderCreateSerializer,
    OrderCustomerDetailSerializer,
    OrderItemInputSerializer,
    OrderLookupRequestSerializer,
)


class CartValidateView(APIView):
    """POST /cart/validate - never persists anything. Each line gets its own
    verdict so the frontend can show which rows are broken before checkout.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CartValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        line_results = []
        subtotal = 0
        any_unpriced = False
        requires_quote = False

        for raw_item in serializer.validated_data['items']:
            item = OrderItemInputSerializer.to_service_item(raw_item)
            result = services.validate_cart_item(item)
            line_results.append(result)

            if item['is_custom']:
                requires_quote = True
            if result['valid']:
                if result['unit_price'] is None:
                    any_unpriced = True
                else:
                    subtotal += result['unit_price'] * item['quantity']
            else:
                any_unpriced = True

        return Response({
            'items': line_results,
            'subtotal': None if any_unpriced else subtotal,
            'requires_quote': requires_quote,
        })


class OrderCreateView(generics.CreateAPIView):
    """POST /orders - public checkout, guest, no account needed."""
    serializer_class = OrderCreateSerializer
    permission_classes = [AllowAny]
    throttle_scope = 'public-write'

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()

        access_token = sign_order_access_token(order.id)
        response_serializer = OrderCreateResponseSerializer(order, context={'access_token': access_token})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class DesignUploadView(APIView):
    """POST /uploads/designs - the custom-print editor uploads here first,
    then references the returned fileToken when placing the order.
    """
    permission_classes = [AllowAny]
    throttle_scope = 'public-write'

    MAX_SIZE = 20 * 1024 * 1024
    ALLOWED_KINDS = {PNG, JPG, SVG, PDF}

    def post(self, request):
        uploaded = request.data.get('file')
        if uploaded is None:
            raise serializers.ValidationError({'file': 'Soubor je povinný.'})
        if uploaded.size > self.MAX_SIZE:
            raise FileTooLargeError()

        data = uploaded.read()
        kind = detect_kind(data)
        if kind not in self.ALLOWED_KINDS:
            raise UnsupportedFileTypeError('Povolené typy jsou PNG, JPG, SVG a PDF.')
        if kind == SVG:
            data = sanitize_svg(data)

        width_px, height_px = get_image_dimensions(data, kind)
        _, url = save_random_file(data, design_upload_directory(), kind)
        payload = {
            'url': url,
            'mime_type': MIME_BY_KIND[kind],
            'size': len(data),
        }
        file_token = sign_file_token(payload)

        return Response({
            'file_token': file_token,
            'url': url,
            'file_name': url.rsplit('/', 1)[-1],
            'size': payload['size'],
            'mime_type': payload['mime_type'],
            'width_px': width_px,
            'height_px': height_px,
        }, status=status.HTTP_201_CREATED)


def _order_queryset():
    return Order.objects.select_related('customer', 'carrier').prefetch_related(
        'items__design_placements__print_area',
    )


class OrderLookupView(APIView):
    """POST /orders/lookup - number + e-mail. A mismatch on either field
    returns the exact same 404, so the response never reveals which part
    was wrong.
    """
    permission_classes = [AllowAny]
    throttle_scope = 'public-write'

    def post(self, request):
        serializer = OrderLookupRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = _order_queryset().filter(
            order_number=serializer.validated_data['order_number'],
            customer__email__iexact=serializer.validated_data['email'],
        ).first()
        if order is None:
            raise NotFound('Objednávka nebyla nalezena.')

        access_token = sign_order_access_token(order.id)
        response_serializer = OrderCustomerDetailSerializer(order, context={'access_token': access_token})
        return Response(response_serializer.data)


class OrderAccessView(APIView):
    """GET /orders/access/{accessToken} - the link sent in confirmation
    e-mails. Never exposed as a plain GET /orders/{id}.
    """
    permission_classes = [AllowAny]

    def get(self, request, access_token):
        try:
            order_id = resolve_order_access_token(access_token)
        except signing.BadSignature:
            raise NotFound('Objednávka nebyla nalezena.')

        order = _order_queryset().filter(id=order_id).first()
        if order is None:
            raise NotFound('Objednávka nebyla nalezena.')

        response_serializer = OrderCustomerDetailSerializer(order, context={'access_token': access_token})
        return Response(response_serializer.data)
