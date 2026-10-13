from django.utils.text import slugify
from rest_framework import serializers

from core.exceptions import FileTooLargeError

from .models import Category, Color, Motif

MOTIF_IMAGE_MAX_SIZE = 10 * 1024 * 1024


class AdminMotifCategoryRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name']


class AdminMotifListSerializer(serializers.ModelSerializer):
    category = AdminMotifCategoryRefSerializer(read_only=True)
    orders_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Motif
        fields = ['id', 'name', 'slug', 'price', 'category', 'position', 'is_active', 'orders_count', 'created_at']


class AdminMotifDetailSerializer(serializers.ModelSerializer):
    category = AdminMotifCategoryRefSerializer(read_only=True)
    orders_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Motif
        fields = [
            'id', 'name', 'slug', 'description', 'price', 'images', 'category',
            'position', 'is_active', 'orders_count', 'created_at', 'updated_at',
        ]


class AdminMotifWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Motif
        fields = ['id', 'name', 'slug', 'description', 'price', 'category', 'position', 'is_active']
        extra_kwargs = {'slug': {'required': False}, 'description': {'required': False}}

    def validate(self, attrs):
        if not attrs.get('slug'):
            name = attrs.get('name') or getattr(self.instance, 'name', '')
            attrs['slug'] = slugify(name)

        queryset = Motif.objects.filter(slug=attrs['slug'])
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError({'slug': 'Tento slug už existuje.'})
        return attrs


class MotifImageUploadSerializer(serializers.Serializer):
    file = serializers.ImageField()
    alt_text = serializers.CharField(required=False, allow_blank=True, default='')
    color_id = serializers.PrimaryKeyRelatedField(
        source='color', queryset=Color.objects.all(), required=False, allow_null=True, default=None,
    )

    def validate_file(self, value):
        if value.size > MOTIF_IMAGE_MAX_SIZE:
            raise FileTooLargeError()
        return value


class MotifImagePatchSerializer(serializers.Serializer):
    alt_text = serializers.CharField(required=False, allow_blank=True)
    color_id = serializers.PrimaryKeyRelatedField(
        source='color', queryset=Color.objects.all(), required=False, allow_null=True,
    )


class MotifImageReorderSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
