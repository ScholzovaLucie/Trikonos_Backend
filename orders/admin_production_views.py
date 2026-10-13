from django.db.models import Sum
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffOrAdmin

from .models import CustomDesignPlacement, OrderItem, OrderStatus

DEFAULT_PRODUCTION_STATUSES = [OrderStatus.PAID, OrderStatus.PROCESSING]

_STATUS_PARAM = OpenApiParameter('status', str, OpenApiParameter.QUERY, description='Čárkou oddělené stavy objednávky.')
_CREATED_FROM_PARAM = OpenApiParameter('createdFrom', str, OpenApiParameter.QUERY, description='ISO datum.')
_CREATED_TO_PARAM = OpenApiParameter('createdTo', str, OpenApiParameter.QUERY, description='ISO datum.')


class ProductionSummaryRowSerializer(serializers.Serializer):
    product_type = serializers.CharField()
    cut = serializers.CharField()
    color = serializers.CharField()
    size = serializers.CharField()
    quantity = serializers.IntegerField()


class ProductionPlacementRowSerializer(serializers.Serializer):
    order_number = serializers.CharField()
    item_label = serializers.CharField()
    print_area = serializers.CharField()
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    rotation_deg = serializers.IntegerField()
    url = serializers.CharField()


class ProductionSummaryView(APIView):
    """Kolik kusů od každé kombinace typ/střih/barva/velikost vyrobit."""
    permission_classes = [IsStaffOrAdmin]

    @extend_schema(
        tags=['Admin - výroba'],
        summary='Souhrn kusů k výrobě podle typu/střihu/barvy/velikosti',
        description=(
            'Součet `quantity` ze všech položek objednávek seskupený podle '
            '`productType`/`cut`/`color`/`size` - slouží jako nákupní seznam '
            'prázdných triček/mikin před tiskem. Filtr `status` (výchozí '
            '`PAID,PROCESSING` - objednávky, které se už opravdu budou '
            'vyrábět), `createdFrom`/`createdTo` pro omezení na konkrétní '
            'období. Zahrnuje jak katalogové, tak vlastní položky - pro '
            'samotné podklady k vlastnímu potisku použij '
            '`/production/placements`.'
        ),
        parameters=[_STATUS_PARAM, _CREATED_FROM_PARAM, _CREATED_TO_PARAM],
        responses={200: ProductionSummaryRowSerializer(many=True)},
    )
    def get(self, request):
        params = request.query_params
        status_param = params.get('status')
        statuses = [value.strip() for value in status_param.split(',')] if status_param else DEFAULT_PRODUCTION_STATUSES

        queryset = OrderItem.objects.filter(order__status__in=statuses)

        created_from = params.get('createdFrom')
        if created_from:
            queryset = queryset.filter(order__created_at__date__gte=created_from)
        created_to = params.get('createdTo')
        if created_to:
            queryset = queryset.filter(order__created_at__date__lte=created_to)

        rows = (
            queryset.values('product_type__name', 'cut__name', 'color__name', 'size')
            .annotate(total_quantity=Sum('quantity'))
            .order_by('product_type__name', 'cut__name', 'color__name', 'size')
        )

        return Response([
            {
                'product_type': row['product_type__name'],
                'cut': row['cut__name'],
                'color': row['color__name'],
                'size': row['size'],
                'quantity': row['total_quantity'],
            }
            for row in rows
        ])


class ProductionPlacementsView(APIView):
    """Seznam umístění vlastního potisku připravených k tisku."""
    permission_classes = [IsStaffOrAdmin]

    @extend_schema(
        tags=['Admin - výroba'],
        summary='Podklady k tisku vlastních potisků',
        description=(
            'Plochý seznam všech umístění vlastního potisku (napříč '
            'objednávkami) - číslo objednávky, popisek položky, název '
            'tiskového bloku, rozměry, rotace a `url` na soubor ke stažení. '
            'Filtry stejné jako u `/production/summary`, navíc `printArea` '
            '(id tiskového bloku, pro zobrazení "všechny potisky na záda '
            'tento týden" apod.).'
        ),
        parameters=[
            _STATUS_PARAM, _CREATED_FROM_PARAM, _CREATED_TO_PARAM,
            OpenApiParameter('printArea', str, OpenApiParameter.QUERY, description='UUID tiskového bloku.'),
        ],
        responses={200: ProductionPlacementRowSerializer(many=True)},
    )
    def get(self, request):
        params = request.query_params
        queryset = CustomDesignPlacement.objects.select_related(
            'print_area', 'order_item', 'order_item__order',
        )

        status_param = params.get('status')
        if status_param:
            statuses = [value.strip() for value in status_param.split(',')]
            queryset = queryset.filter(order_item__order__status__in=statuses)

        print_area_id = params.get('printArea')
        if print_area_id:
            queryset = queryset.filter(print_area_id=print_area_id)

        created_from = params.get('createdFrom')
        if created_from:
            queryset = queryset.filter(order_item__order__created_at__date__gte=created_from)
        created_to = params.get('createdTo')
        if created_to:
            queryset = queryset.filter(order_item__order__created_at__date__lte=created_to)

        return Response([
            {
                'order_number': placement.order_item.order.order_number,
                'item_label': placement.order_item.item_label,
                'print_area': placement.print_area.name,
                'width': placement.width,
                'height': placement.height,
                'rotation_deg': placement.rotation_deg,
                'url': placement.url,
            }
            for placement in queryset.order_by('order_item__order__created_at')
        ])
