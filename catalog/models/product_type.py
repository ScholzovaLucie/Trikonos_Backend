from core.models import SimpleLookup


class ProductType(SimpleLookup):
    """T-shirt, Hoodie, Tank top, Accessory, ..."""

    class Meta(SimpleLookup.Meta):
        db_table = 'product_types'
