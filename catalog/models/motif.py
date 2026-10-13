from django.db import models

from core.models import SimpleLookup
from .category import Category


class Motif(SimpleLookup):
    """Catalog motif/design. The customer picks a motif; cut/color/size/
    product type are chosen separately on the order item.
    """
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True, default='')
    price = models.PositiveIntegerField(help_text='Price in the smallest currency unit (haléře)')
    # Element shape: {"url": "...", "altText": "...", "position": 0, "colorId": "uuid or null"}.
    # "colorId" refers to Color but is validated in clean(), not a real FK (JSONField can't do that).
    images = models.JSONField(default=list, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='motifs')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(SimpleLookup.Meta):
        db_table = 'motifs'
        constraints = [
            models.CheckConstraint(condition=models.Q(price__gte=0), name='motif_price_non_negative'),
        ]
