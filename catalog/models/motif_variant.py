from django.db import models

from core.models import UUIDModel

from .color import Color
from .cut import Cut
from .motif import Motif
from .product_type import ProductType


class MotifVariant(UUIDModel):
    """One orderable (product type, cut, color, size) combination for a
    motif. Admin opts combinations in explicitly - without a matching row
    here, that combination is rejected at checkout (see
    orders.services._validate_and_price_item).

    `size` isn't an FK or a model-level `choices=` on purpose: orders.models.Size
    (the actual S/M/L/XL/XXL enum) lives in the orders app, and orders already
    imports from catalog - importing it back here at module load time would
    create a circular import between the two apps' models packages. Valid
    values are enforced one layer up, in the admin serializer, where a local
    import is safe (it runs after both apps have finished loading).
    """
    motif = models.ForeignKey(Motif, on_delete=models.CASCADE, related_name='variants')
    product_type = models.ForeignKey(ProductType, on_delete=models.CASCADE, related_name='+')
    cut = models.ForeignKey(Cut, on_delete=models.CASCADE, related_name='+')
    color = models.ForeignKey(Color, on_delete=models.CASCADE, related_name='+')
    size = models.CharField(max_length=4)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'motif_variants'
        unique_together = [('motif', 'product_type', 'cut', 'color', 'size')]

    def __str__(self):
        return f'{self.motif.name} / {self.product_type.name} / {self.cut.name} / {self.color.name} / {self.size}'
