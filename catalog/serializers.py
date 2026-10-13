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


class MotifDetailSerializer(serializers.ModelSerializer):
    category = MotifCategoryRefSerializer(read_only=True)
    currency = serializers.SerializerMethodField()

    class Meta:
        model = Motif
        fields = ['id', 'slug', 'name', 'description', 'price', 'currency', 'images', 'category']

    def get_currency(self, obj) -> str:
        return 'CZK'


class CatalogOptionPrintAreaSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    width_mm = serializers.IntegerField()
    height_mm = serializers.IntegerField()


class CatalogOptionProductTypeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
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
