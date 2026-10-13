from django.core.exceptions import ValidationError
from django.db import models

from core.models import UUIDModel
from catalog.models import PrintArea
from .order_item import OrderItem


class CustomDesignPlacement(UUIDModel):
    """Custom-print placement, stored as relational rows (not JSONB) so
    print_area is a real foreign key and admin can report across orders
    (e.g. "all back prints this week"). One custom item can have several
    rows here (multiple images/text blocks on different areas of one shirt).
    """
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name='design_placements')
    print_area = models.ForeignKey(PrintArea, on_delete=models.PROTECT, related_name='placements')
    url = models.URLField(max_length=500, help_text='File uploaded by the customer')
    x = models.IntegerField(default=0, help_text='Position within the area, same unit as print_area.width_mm/height_mm')
    y = models.IntegerField(default=0)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    rotation_deg = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'custom_design_placements'
        constraints = [
            models.CheckConstraint(condition=models.Q(width__gt=0), name='placement_width_positive'),
            models.CheckConstraint(condition=models.Q(height__gt=0), name='placement_height_positive'),
        ]

    def clean(self):
        # Mirrors the check_custom_design_placement_product_type SQL trigger -
        # a cross-table rule Meta.constraints can't express. Catching it here
        # surfaces the error in the admin/DRF serializer instead of at INSERT.
        if self.order_item_id and self.print_area_id:
            if self.order_item.product_type_id != self.print_area.product_type_id:
                raise ValidationError(
                    'print_area belongs to a different product type than its order_item.'
                )
