from django.db import models

from core.models import UUIDModel
from orders.models import Order


class Payment(UUIDModel):
    class Provider(models.TextChoices):
        GOPAY = 'GOPAY', 'GoPay'
        COMGATE = 'COMGATE', 'Comgate'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Čeká se'
        PAID = 'PAID', 'Zaplaceno'
        FAILED = 'FAILED', 'Selhalo'
        REFUNDED = 'REFUNDED', 'Vráceno'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    provider = models.CharField(max_length=10, choices=Provider.choices)
    provider_payment_id = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    amount = models.IntegerField(help_text='Cena v haléřích')
    raw_payload = models.JSONField(null=True, blank=True, help_text='Odpověď/webhook z brány')
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payments'
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gte=0), name='ck_payments_amount_nonneg'),
            models.UniqueConstraint(
                fields=['provider', 'provider_payment_id'],
                condition=models.Q(provider_payment_id__isnull=False),
                name='uq_payments_provider_payment_id',
            ),
        ]

    def __str__(self):
        return f'{self.order} / {self.provider}'
