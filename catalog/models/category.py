from django.db import models

from core.models import SimpleLookup


class Category(SimpleLookup):
    """Navigation category on the storefront (Men's shirts, Collections, ...)."""
    slug = models.SlugField(max_length=255, unique=True)

    class Meta(SimpleLookup.Meta):
        db_table = 'categories'
        verbose_name_plural = 'categories'
