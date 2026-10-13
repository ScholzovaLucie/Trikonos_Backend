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

    def get_currency(self, obj):
        return 'CZK'

    def get_main_image(self, obj):
        images = obj.images or []
        return images[0]['url'] if images else None


class MotifDetailSerializer(serializers.ModelSerializer):
    category = MotifCategoryRefSerializer(read_only=True)
    currency = serializers.SerializerMethodField()

    class Meta:
        model = Motif
        fields = ['id', 'slug', 'name', 'description', 'price', 'currency', 'images', 'category']

    def get_currency(self, obj):
        return 'CZK'
