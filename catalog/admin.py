from django.contrib import admin

from .models import (
    Category, Collection, Color, Cut, GarmentType, Motif, Product,
    ProductImage, ProductVariant,
)


@admin.register(Cut)
class CutAdmin(admin.ModelAdmin):
    list_display = ['name', 'position', 'is_active']
    list_editable = ['position', 'is_active']


@admin.register(Color)
class ColorAdmin(admin.ModelAdmin):
    list_display = ['name', 'hex_value', 'position', 'is_active']
    list_editable = ['position', 'is_active']


@admin.register(GarmentType)
class GarmentTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'position', 'is_active']
    list_editable = ['position', 'is_active']


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'position']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Motif)
class MotifAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'garment_type', 'base_price', 'is_active']
    list_filter = ['category', 'garment_type', 'is_active']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline, ProductVariantInline]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ['sku', 'product', 'cut', 'color', 'size', 'stock_quantity']
    list_filter = ['cut', 'color', 'size']
    search_fields = ['sku']
