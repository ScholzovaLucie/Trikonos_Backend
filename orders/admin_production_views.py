from django.db.models import Sum
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffOrAdmin

from .models import CustomDesignPlacement, OrderItem, OrderStatus

DEFAULT_PRODUCTION_STATUSES = [OrderStatus.PAID, OrderStatus.PROCESSING]


class ProductionSummaryView(APIView):
    """GET /production/summary - how many of each type/cut/color/size to
    produce; doubles as the blank-shirt shopping list.
    """
    permission_classes = [IsStaffOrAdmin]

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
    """GET /production/placements - every custom-print placement to produce,
    with a link to the uploaded file."""
    permission_classes = [IsStaffOrAdmin]

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
