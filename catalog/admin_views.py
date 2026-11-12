from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import serializers, viewsets

from accounts.permissions import IsAdmin
from core.admin_lookup import LookupAdminViewSet, lookup_schema_view
from core.pagination import AdminPagination

from .admin_serializers import (
    AdminCategorySerializer,
    AdminColorSerializer,
    AdminCutSerializer,
    AdminMotifVariantSerializer,
    AdminPrintAreaSerializer,
    AdminProductTypeSerializer,
)
from .models import Category, Color, Cut, MotifVariant, PrintArea, ProductType


@lookup_schema_view(
    'kategorii', 'kategorií',
    extra_note='`slug` se při vynechání vygeneruje automaticky z `name` (musí být unikátní).',
)
class AdminCategoryViewSet(LookupAdminViewSet):
    """Navigační kategorie motivů (Pánská trička, Kolekce, ...)."""
    queryset = Category.objects.all()
    serializer_class = AdminCategorySerializer
    search_fields = ['name', 'slug']


@lookup_schema_view(
    'barvu', 'barev',
    extra_note='`hexValue` musí být ve formátu `#RRGGBB`.',
    tag='Admin - varianty',
)
class AdminColorViewSet(LookupAdminViewSet):
    """Barvy oblečení, nabízené při výběru produktu."""
    queryset = Color.objects.all()
    serializer_class = AdminColorSerializer


@lookup_schema_view('střih', 'střihů', tag='Admin - varianty')
class AdminCutViewSet(LookupAdminViewSet):
    """Střihy (Pánské, Dámské, Unisex, ...)."""
    queryset = Cut.objects.all()
    serializer_class = AdminCutSerializer


@lookup_schema_view(
    'typ oblečení', 'typů oblečení',
    extra_note='Detail obsahuje zanořené `printAreas` daného typu.',
)
class AdminProductTypeViewSet(LookupAdminViewSet):
    """Typy oblečení (Tričko, Mikina, ...) - každý má vlastní sadu tiskových bloků."""
    queryset = ProductType.objects.all().prefetch_related('print_areas')
    serializer_class = AdminProductTypeSerializer


@extend_schema_view(
    list=extend_schema(
        tags=['Admin - číselníky'],
        summary='Seznam tiskových bloků',
        description=(
            'Vrátí všechny tiskové bloky včetně neaktivních. Navíc k `isActive`/`q` '
            'podporuje filtr `productType` (id typu oblečení) pro zobrazení jen '
            'bloků patřících danému typu.'
        ),
        parameters=[OpenApiParameter('productType', str, OpenApiParameter.QUERY, description='UUID typu oblečení.')],
    ),
)
@lookup_schema_view(
    'tiskový blok', 'tiskových bloků',
    extra_note=(
        '`productTypeId` je povinné, `widthMm`/`heightMm` musí být kladné a '
        '`name` musí být unikátní v rámci daného `productType` (ne globálně - '
        '"Přední strana" může existovat pro Tričko i Mikinu zároveň).'
    ),
)
class AdminPrintAreaViewSet(LookupAdminViewSet):
    """Místa pro umístění potisku v editoru vlastního potisku, vázaná na typ oblečení."""
    queryset = PrintArea.objects.select_related('product_type').all()
    serializer_class = AdminPrintAreaSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        product_type_id = self.request.query_params.get('productType')
        if product_type_id:
            queryset = queryset.filter(product_type_id=product_type_id)
        return queryset


@extend_schema_view(
    list=extend_schema(
        tags=['Admin - motivy'],
        summary='Seznam dostupných kombinací pro motiv',
        description=(
            'Vyžaduje `motif` (UUID) v query - bez něj 400. Vrátí kombinace '
            '(typ oblečení/střih/barva/velikost), které jsou pro tento motiv '
            'objednatelné; bez odpovídajícího řádku zde `POST /orders` i '
            '`POST /cart/validate` tu kombinaci zamítnou (422 '
            '`VARIANT_NOT_AVAILABLE`).'
        ),
        parameters=[OpenApiParameter('motif', str, OpenApiParameter.QUERY, description='UUID motivu.', required=True)],
    ),
    create=extend_schema(
        tags=['Admin - motivy'],
        summary='Povolit kombinaci pro motiv',
        description='Duplicitní kombinace (stejný motiv/typ/střih/barva/velikost) vrátí 409 `DUPLICATE_VARIANT`.',
    ),
    partial_update=extend_schema(tags=['Admin - motivy'], summary='Upravit kombinaci (např. deaktivovat)'),
    destroy=extend_schema(tags=['Admin - motivy'], summary='Smazat kombinaci'),
)
class AdminMotifVariantViewSet(viewsets.ModelViewSet):
    """Které typ/střih/barva/velikost kombinace jsou objednatelné pro daný motiv."""
    serializer_class = AdminMotifVariantSerializer
    permission_classes = [IsAdmin]
    pagination_class = AdminPagination
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    filter_backends = []

    def get_queryset(self):
        queryset = MotifVariant.objects.select_related('motif', 'product_type', 'cut', 'color')
        motif_id = self.request.query_params.get('motif')
        if not motif_id:
            # Only the unscoped list needs ?motif= - retrieve/update/destroy
            # already pin down a single row by pk and don't need the filter.
            if self.action == 'list':
                raise serializers.ValidationError({'motif': _('This query parameter is required.')})
            return queryset.order_by('product_type__name', 'cut__name', 'color__name', 'size')
        return queryset.filter(motif_id=motif_id).order_by('product_type__name', 'cut__name', 'color__name', 'size')
