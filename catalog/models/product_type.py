from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import SimpleLookup


class ProductType(SimpleLookup):
    """T-shirt, Hoodie, Tank top, Accessory, ... Added to motif.price when
    pricing a catalog order item - the same motif costs more printed on a
    hoodie than on a t-shirt.
    """
    price = models.PositiveIntegerField(default=0, help_text='Price in the smallest currency unit (haléře)')

    class Meta(SimpleLookup.Meta):
        db_table = 'product_types'
        verbose_name = _('product type')
        verbose_name_plural = _('product types')
        constraints = [
            models.CheckConstraint(condition=models.Q(price__gte=0), name='product_type_price_non_negative'),
        ]
