from django.db import models

from core.models import UUIDModel
from orders.models import Order


class PohodaSyncLog(UUIDModel):
    class Direction(models.TextChoices):
        EXPORT_ORDER = 'EXPORT_ORDER', 'Export objednávky'
        IMPORT_STOCK = 'IMPORT_STOCK', 'Import skladu'

    class Status(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Úspěch'
        FAILED = 'FAILED', 'Selhalo'
        PENDING = 'PENDING', 'Čeká se'

    order = models.ForeignKey(
        Order, on_delete=models.PROTECT, related_name='pohoda_sync_logs', null=True, blank=True,
    )
    direction = models.CharField(max_length=15, choices=Direction.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    payload = models.JSONField(null=True, blank=True, help_text='Odeslané/přijaté XML nebo jeho shrnutí')
    error_message = models.TextField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pohoda_sync_logs'

    def __str__(self):
        return f'{self.direction} / {self.status}'
