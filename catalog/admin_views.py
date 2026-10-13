from core.admin_lookup import LookupAdminViewSet

from .admin_serializers import (
    AdminCategorySerializer,
    AdminColorSerializer,
    AdminCutSerializer,
    AdminPrintAreaSerializer,
    AdminProductTypeSerializer,
)
from .models import Category, Color, Cut, PrintArea, ProductType


class AdminCategoryViewSet(LookupAdminViewSet):
    queryset = Category.objects.all()
    serializer_class = AdminCategorySerializer
    search_fields = ['name', 'slug']


class AdminColorViewSet(LookupAdminViewSet):
    queryset = Color.objects.all()
    serializer_class = AdminColorSerializer


class AdminCutViewSet(LookupAdminViewSet):
    queryset = Cut.objects.all()
    serializer_class = AdminCutSerializer


class AdminProductTypeViewSet(LookupAdminViewSet):
    queryset = ProductType.objects.all().prefetch_related('print_areas')
    serializer_class = AdminProductTypeSerializer


class AdminPrintAreaViewSet(LookupAdminViewSet):
    queryset = PrintArea.objects.select_related('product_type').all()
    serializer_class = AdminPrintAreaSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        product_type_id = self.request.query_params.get('productType')
        if product_type_id:
            queryset = queryset.filter(product_type_id=product_type_id)
        return queryset
