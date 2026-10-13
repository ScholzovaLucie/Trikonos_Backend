from datetime import timedelta

from django.db.models import Count, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import Size
from shipping.models import Carrier

from .models import Category, Color, Cut, Motif, PrintArea, ProductType
from .serializers import CategorySerializer, MotifDetailSerializer, MotifListSerializer

RELATED_MOTIFS_LIMIT = 8


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /categories, GET /categories/{slug} - storefront navigation."""
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]
    pagination_class = None
    lookup_field = 'slug'
    lookup_value_regex = '[^/]+'

    def get_queryset(self):
        return (
            Category.objects.filter(is_active=True)
            .annotate(motif_count=Count('motifs', filter=Q(motifs__is_active=True)))
            .order_by('position', 'name')
        )


class MotifViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /motifs, GET /motifs/{slug}, GET /motifs/{slug}/related."""
    permission_classes = [AllowAny]
    lookup_field = 'slug'
    lookup_value_regex = '[^/]+'
    filter_backends = []  # category/q/ids/ordering are handled explicitly below

    def get_serializer_class(self):
        if self.action == 'list':
            return MotifListSerializer
        return MotifDetailSerializer

    def get_queryset(self):
        queryset = Motif.objects.filter(is_active=True).select_related('category')
        params = self.request.query_params

        category_slug = params.get('category')
        if category_slug:
            queryset = queryset.filter(category__slug=category_slug)

        search_term = params.get('q')
        if search_term:
            queryset = queryset.filter(Q(name__icontains=search_term) | Q(description__icontains=search_term))

        ids_param = params.get('ids')
        if ids_param:
            ids = [value.strip() for value in ids_param.split(',') if value.strip()]
            queryset = queryset.filter(id__in=ids)

        return self._ordered(queryset, params.get('ordering', 'position'))

    @staticmethod
    def _ordered(queryset, ordering):
        if ordering == '-createdAt':
            return queryset.order_by('-created_at')
        if ordering == 'price':
            return queryset.order_by('price')
        if ordering == '-price':
            return queryset.order_by('-price')
        if ordering == 'name':
            return queryset.order_by('name')
        if ordering == 'popular':
            since = timezone.now() - timedelta(days=90)
            return queryset.annotate(
                units_sold=Coalesce(
                    Sum('order_items__quantity', filter=Q(order_items__order__created_at__gte=since)),
                    0,
                )
            ).order_by('-units_sold', 'position')
        return queryset.order_by('position', 'name')

    @action(detail=True, methods=['get'])
    def related(self, request, slug=None):
        """Max 8 active motifs from the same category, topped up with the
        newest overall if the category doesn't have enough.
        """
        motif = self.get_object()
        same_category = list(
            Motif.objects.filter(is_active=True, category=motif.category)
            .exclude(id=motif.id)
            .order_by('-created_at')[:RELATED_MOTIFS_LIMIT]
        )
        if len(same_category) < RELATED_MOTIFS_LIMIT:
            exclude_ids = [motif.id] + [item.id for item in same_category]
            fillers = Motif.objects.filter(is_active=True).exclude(id__in=exclude_ids).order_by('-created_at')
            same_category += list(fillers[:RELATED_MOTIFS_LIMIT - len(same_category)])

        serializer = MotifListSerializer(same_category, many=True, context=self.get_serializer_context())
        return Response(serializer.data)


class CatalogOptionsView(APIView):
    """GET /catalog/options - every active lookup the configurator/editor/
    checkout needs, in a single call.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        product_types = ProductType.objects.filter(is_active=True).order_by('position', 'name').prefetch_related(
            Prefetch(
                'print_areas',
                queryset=PrintArea.objects.filter(is_active=True).order_by('position', 'name'),
            )
        )
        cuts = Cut.objects.filter(is_active=True).order_by('position', 'name')
        colors = Color.objects.filter(is_active=True).order_by('position', 'name')
        carriers = Carrier.objects.filter(is_active=True).order_by('position', 'name')

        return Response({
            'product_types': [
                {
                    'id': product_type.id,
                    'name': product_type.name,
                    'print_areas': [
                        {
                            'id': area.id,
                            'name': area.name,
                            'width_mm': area.width_mm,
                            'height_mm': area.height_mm,
                        }
                        for area in product_type.print_areas.all()
                    ],
                }
                for product_type in product_types
            ],
            'cuts': [{'id': cut.id, 'name': cut.name} for cut in cuts],
            'colors': [{'id': color.id, 'name': color.name, 'hex_value': color.hex_value} for color in colors],
            'sizes': [{'value': value, 'label': label} for value, label in Size.choices],
            'carriers': [{'id': carrier.id, 'name': carrier.name, 'code': carrier.code} for carrier in carriers],
        })
