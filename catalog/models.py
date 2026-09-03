from django.db import models

from core.models import UUIDModel, TimestampedModel


# -----------------------------------------------------------------------------
# ČÍSELNÍKY (admin obrazovka "Číselníky" — editovatelné za běhu, ne ENUM)
# -----------------------------------------------------------------------------

class Cut(UUIDModel):
    """Střih: Pánské, Dámské, Unisex, Mikina, ..."""

    name = models.TextField(unique=True)
    position = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'cuts'
        ordering = ['position']

    def __str__(self):
        return self.name


class Color(UUIDModel):
    """Barva: Černá, Bílá, Žlutá, ..."""

    name = models.TextField(unique=True)
    hex_value = models.TextField()
    position = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'colors'
        ordering = ['position']

    def __str__(self):
        return self.name


class GarmentType(UUIDModel):
    """Typ oblečení: Tričko, Mikina, Tílko, Doplněk, ..."""

    name = models.TextField(unique=True)
    position = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'garment_types'
        ordering = ['position']

    def __str__(self):
        return self.name


# -----------------------------------------------------------------------------
# KATALOG
# -----------------------------------------------------------------------------

class Category(UUIDModel):
    name = models.TextField()
    slug = models.SlugField(max_length=255, unique=True)
    position = models.IntegerField(default=0)

    class Meta:
        db_table = 'categories'
        ordering = ['position']
        verbose_name_plural = 'categories'

    def __str__(self):
        return self.name


class Motif(UUIDModel):
    name = models.TextField()
    slug = models.SlugField(max_length=255, unique=True)

    class Meta:
        db_table = 'motifs'

    def __str__(self):
        return self.name


class Collection(UUIDModel):
    name = models.TextField()
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'collections'

    def __str__(self):
        return self.name


class Product(UUIDModel, TimestampedModel):
    name = models.TextField()
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(null=True, blank=True)
    base_price = models.IntegerField(help_text='Cena v haléřích')
    is_active = models.BooleanField(default=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    garment_type = models.ForeignKey(GarmentType, on_delete=models.PROTECT, related_name='products')
    motifs = models.ManyToManyField(Motif, through='ProductMotif', related_name='products')
    collections = models.ManyToManyField(Collection, through='ProductCollection', related_name='products')

    class Meta:
        db_table = 'products'
        constraints = [
            models.CheckConstraint(check=models.Q(base_price__gte=0), name='ck_products_base_price_nonneg'),
        ]

    def __str__(self):
        return self.name


class ProductImage(UUIDModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    color = models.ForeignKey(
        Color, on_delete=models.PROTECT, related_name='product_images', null=True, blank=True,
        help_text='NULL = platí pro všechny barvy, jinak jen pro danou barvu',
    )
    url = models.TextField()
    alt_text = models.TextField(null=True, blank=True)
    position = models.IntegerField(default=0)

    class Meta:
        db_table = 'product_images'
        ordering = ['position']

    def __str__(self):
        return f'{self.product} #{self.position}'


class ProductVariant(UUIDModel):
    class Size(models.TextChoices):
        S = 'S', 'S'
        M = 'M', 'M'
        L = 'L', 'L'
        XL = 'XL', 'XL'
        XXL = 'XXL', 'XXL'

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    sku = models.TextField(unique=True)
    cut = models.ForeignKey(Cut, on_delete=models.PROTECT, related_name='product_variants')
    color = models.ForeignKey(Color, on_delete=models.PROTECT, related_name='product_variants')
    size = models.CharField(max_length=3, choices=Size.choices)
    price_override = models.IntegerField(null=True, blank=True)
    stock_quantity = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'product_variants'
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'cut', 'color', 'size'], name='uq_variants_product_cut_color_size',
            ),
            models.CheckConstraint(
                check=models.Q(price_override__isnull=True) | models.Q(price_override__gte=0),
                name='ck_variants_price_override_nonneg',
            ),
            models.CheckConstraint(check=models.Q(stock_quantity__gte=0), name='ck_variants_stock_nonneg'),
        ]

    def __str__(self):
        return self.sku


class ProductMotif(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    motif = models.ForeignKey(Motif, on_delete=models.CASCADE)

    class Meta:
        db_table = 'product_motifs'
        constraints = [
            models.UniqueConstraint(fields=['product', 'motif'], name='pk_product_motifs'),
        ]


class ProductCollection(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE)

    class Meta:
        db_table = 'product_collections'
        constraints = [
            models.UniqueConstraint(fields=['product', 'collection'], name='pk_product_collections'),
        ]
