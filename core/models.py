import uuid

from django.db import models


class UUIDModel(models.Model):
    """UUID primary key, matching `DEFAULT gen_random_uuid()` in the SQL schema."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class SimpleLookup(UUIDModel):
    """Shared shape for lookup tables: name/position/is_active."""
    name = models.CharField(max_length=255, unique=True)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ['position']

    def __str__(self):
        return self.name
