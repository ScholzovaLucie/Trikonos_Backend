from django.db import models

from core.models import SimpleLookup
from .product_type import ProductType


class PrintArea(SimpleLookup):
    """Placement slot available in the custom-print editor. Varies by
    product type (a t-shirt has sleeves, a cap doesn't).
    """
    product_type = models.ForeignKey(ProductType, on_delete=models.CASCADE, related_name='print_areas')
    width_mm = models.PositiveIntegerField(help_text='Print area size, used by both the editor and the printer')
    height_mm = models.PositiveIntegerField()

    class Meta(SimpleLookup.Meta):
        db_table = 'print_areas'
        constraints = [
            models.UniqueConstraint(fields=['product_type', 'name'], name='uq_print_area_product_type_name'),
            models.CheckConstraint(condition=models.Q(width_mm__gt=0), name='print_area_width_positive'),
            models.CheckConstraint(condition=models.Q(height_mm__gt=0), name='print_area_height_positive'),
        ]
