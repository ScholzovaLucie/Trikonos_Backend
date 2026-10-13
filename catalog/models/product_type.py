from django.utils.translation import gettext_lazy as _

from core.models import SimpleLookup


class ProductType(SimpleLookup):
    """T-shirt, Hoodie, Tank top, Accessory, ..."""

    class Meta(SimpleLookup.Meta):
        db_table = 'product_types'
        verbose_name = _('product type')
        verbose_name_plural = _('product types')
