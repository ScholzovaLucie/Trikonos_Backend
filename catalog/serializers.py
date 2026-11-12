from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Category, Motif


class CategorySerializer(serializers.ModelSerializer):
    """Public shape. `motif_count` is annotated on the viewset's queryset -
    counting only active motifs, not a raw reverse-FK count.
    """
    motif_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'position', 'motif_count']


class MotifCategoryRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['name', 'slug']


class MotifListSerializer(serializers.ModelSerializer):
    category = MotifCategoryRefSerializer(read_only=True)
    currency = serializers.SerializerMethodField()
    main_image = serializers.SerializerMethodField()

    class Meta:
        model = Motif
        fields = ['id', 'slug', 'name', 'price', 'currency', 'main_image', 'category']

    def get_currency(self, obj) -> str:
        return 'CZK'

    def get_main_image(self, obj) -> str | None:
        images = obj.images or []
        return images[0]['url'] if images else None


class MotifVariantRefSerializer(serializers.Serializer):
    product_type_id = serializers.UUIDField()
    cut_id = serializers.UUIDField()
    color_id = serializers.UUIDField()
    size = serializers.CharField()


class MotifDetailSerializer(serializers.ModelSerializer):
    category = MotifCategoryRefSerializer(read_only=True)
    currency = serializers.SerializerMethodField()
    variants = serializers.SerializerMethodField()

    class Meta:
        model = Motif
        fields = ['id', 'slug', 'name', 'description', 'price', 'currency', 'images', 'category', 'variants']

    def get_currency(self, obj) -> str:
        return 'CZK'

    @extend_schema_field(MotifVariantRefSerializer(many=True))
    def get_variants(self, obj):
        """Which productType/cut/color/size combinations are orderable for
        this motif - admin opts them in via /admin/motif-variants. The
        configurator uses this to show only buyable combinations instead of
        letting the customer pick one that /cart/validate will then reject.
        """
        return MotifVariantRefSerializer(obj.variants.filter(is_active=True), many=True).data


class CatalogOptionPrintAreaSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    width_mm = serializers.IntegerField()
    height_mm = serializers.IntegerField()


class CatalogOptionProductTypeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    price = serializers.IntegerField()
    print_areas = CatalogOptionPrintAreaSerializer(many=True)


class CatalogOptionCutSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class CatalogOptionColorSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    hex_value = serializers.CharField()


class CatalogOptionSizeSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class CatalogOptionCarrierSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    code = serializers.CharField()


class CatalogOptionsResponseSerializer(serializers.Serializer):
    product_types = CatalogOptionProductTypeSerializer(many=True)
    cuts = CatalogOptionCutSerializer(many=True)
    colors = CatalogOptionColorSerializer(many=True)
    sizes = CatalogOptionSizeSerializer(many=True)
    carriers = CatalogOptionCarrierSerializer(many=True)
