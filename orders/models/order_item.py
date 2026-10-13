from django.core.exceptions import ValidationError
from django.db import models

from core.models import UUIDModel
from catalog.models import Color, Cut, Motif, ProductType
from .choices import Size
from .order import Order


class OrderItem(UUIDModel):
    """One ordered line (one shirt/hoodie/accessory). Exactly one of two
    paths per item, never mixed:
      - is_custom=False -> motif (always exactly one) from the catalog,
        price = motif.price
      - is_custom=True  -> custom_text + CustomDesignPlacement rows (several
        placements possible), price always set manually by admin
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    is_custom = models.BooleanField(default=False)
    motif = models.ForeignKey(Motif, on_delete=models.PROTECT, null=True, blank=True, related_name='order_items')
    custom_text = models.TextField(blank=True, default='', help_text='Free-form print instructions, only for is_custom=True')

    product_type = models.ForeignKey(ProductType, on_delete=models.PROTECT, related_name='order_items')
    cut = models.ForeignKey(Cut, on_delete=models.PROTECT, related_name='order_items')
    color = models.ForeignKey(Color, on_delete=models.PROTECT, related_name='order_items')
    size = models.CharField(max_length=3, choices=Size.choices)

    item_label = models.CharField(max_length=255, help_text='Snapshot, e.g. "Bitcoin / Men / Black / M"')
    unit_price = models.PositiveIntegerField(null=True, blank=True, help_text='motif.price (catalog) or NULL until admin prices a custom item')
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = 'order_items'
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='order_item_quantity_positive'),
            models.CheckConstraint(
                condition=(
                    (models.Q(is_custom=False) & models.Q(motif__isnull=False))
                    | (models.Q(is_custom=True) & models.Q(motif__isnull=True))
                ),
                name='order_item_custom_xor_motif',
            ),
        ]

    def clean(self):
        if self.is_custom and self.motif_id:
            raise ValidationError('A custom item cannot have a catalog motif assigned.')
        if not self.is_custom and not self.motif_id:
            raise ValidationError('A catalog item must have a motif assigned.')

    def __str__(self):
        return self.item_label
