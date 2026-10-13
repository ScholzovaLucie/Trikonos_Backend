import re

from django.utils.text import slugify
from rest_framework import serializers

from .models import Category, Color, Cut, PrintArea, ProductType


class AdminCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'position', 'is_active']
        extra_kwargs = {'slug': {'required': False}}

    def validate(self, attrs):
        if not attrs.get('slug'):
            name = attrs.get('name') or getattr(self.instance, 'name', '')
            attrs['slug'] = slugify(name)

        queryset = Category.objects.filter(slug=attrs['slug'])
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError({'slug': 'Tento slug už existuje, zadejte jiný název nebo slug.'})
        return attrs


class AdminColorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Color
        fields = ['id', 'name', 'hex_value', 'position', 'is_active']

    def validate_hex_value(self, value):
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}', value):
            raise serializers.ValidationError('Barva musí být ve formátu #RRGGBB.')
        return value.upper()


class AdminCutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cut
        fields = ['id', 'name', 'position', 'is_active']


class AdminPrintAreaRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintArea
        fields = ['id', 'name', 'width_mm', 'height_mm', 'position', 'is_active']


class AdminProductTypeSerializer(serializers.ModelSerializer):
    print_areas = AdminPrintAreaRefSerializer(many=True, read_only=True)

    class Meta:
        model = ProductType
        fields = ['id', 'name', 'position', 'is_active', 'print_areas']


class AdminPrintAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintArea
        fields = ['id', 'name', 'product_type', 'width_mm', 'height_mm', 'position', 'is_active']

    def validate(self, attrs):
        width = attrs.get('width_mm', getattr(self.instance, 'width_mm', None))
        height = attrs.get('height_mm', getattr(self.instance, 'height_mm', None))
        if width is not None and width <= 0:
            raise serializers.ValidationError({'width_mm': 'Rozměr musí být kladný.'})
        if height is not None and height <= 0:
            raise serializers.ValidationError({'height_mm': 'Rozměr musí být kladný.'})

        product_type = attrs.get('product_type', getattr(self.instance, 'product_type', None))
        name = attrs.get('name', getattr(self.instance, 'name', None))
        queryset = PrintArea.objects.filter(product_type=product_type, name=name)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError({
                'name': 'Tiskový blok s tímto názvem už u tohoto typu oblečení existuje.',
            })
        return attrs
