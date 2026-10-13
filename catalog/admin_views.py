from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view

from core.admin_lookup import LookupAdminViewSet, lookup_schema_view

from .admin_serializers import (
    AdminCategorySerializer,
    AdminColorSerializer,
    AdminCutSerializer,
    AdminPrintAreaSerializer,
    AdminProductTypeSerializer,
)
from .models import Category, Color, Cut, PrintArea, ProductType


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
)
class AdminColorViewSet(LookupAdminViewSet):
    """Barvy oblečení, nabízené při výběru produktu."""
    queryset = Color.objects.all()
    serializer_class = AdminColorSerializer


@lookup_schema_view('střih', 'střihů')
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
