from django.db import models

from core.models import TimestampedModel, UUIDModel


class Carrier(UUIDModel):
    """Dopravci (Zásilkovna, DPD, Česká pošta, ...) — číselník, admin přidává bez migrace."""

    name = models.TextField(unique=True)
    code = models.TextField(unique=True, help_text="Interní kód, např. 'PACKETA'")
    supports_pickup_point = models.BooleanField(default=False)
    supports_home_delivery = models.BooleanField(default=False)
    position = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'carriers'
        ordering = ['position']

    def __str__(self):
        return self.name


class DeliveryType(models.TextChoices):
    PICKUP_POINT = 'PICKUP_POINT', 'Výdejní místo'
    HOME_DELIVERY = 'HOME_DELIVERY', 'Doručení na adresu'


class Shipment(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        CREATED = 'CREATED', 'Vytvořeno'
        LABEL_PRINTED = 'LABEL_PRINTED', 'Štítek vytištěn'
        HANDED_OVER = 'HANDED_OVER', 'Předáno dopravci'
        IN_TRANSIT = 'IN_TRANSIT', 'V přepravě'
        DELIVERED = 'DELIVERED', 'Doručeno'
        RETURNED = 'RETURNED', 'Vráceno'

    order = models.ForeignKey('orders.Order', on_delete=models.CASCADE, related_name='shipments')
    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, related_name='shipments')
    tracking_number = models.TextField(null=True, blank=True)
    label_url = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.CREATED)

    class Meta:
        db_table = 'shipments'

    def __str__(self):
        return self.tracking_number or str(self.id)


class ShippingRate(UUIDModel):
    """Cena a práh dopravy zdarma per dopravce + typ doručení, editovatelné bez nasazení."""

    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, related_name='shipping_rates')
    delivery_type = models.CharField(max_length=15, choices=DeliveryType.choices)
    price = models.IntegerField(help_text='Cena v haléřích')
    free_from = models.IntegerField(null=True, blank=True, help_text='NULL = doprava nikdy není zdarma')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shipping_rates'
        constraints = [
            models.UniqueConstraint(fields=['carrier', 'delivery_type'], name='uq_shipping_rates_carrier_type'),
            models.CheckConstraint(check=models.Q(price__gte=0), name='ck_shipping_rates_price_nonneg'),
            models.CheckConstraint(
                check=models.Q(free_from__isnull=True) | models.Q(free_from__gte=0),
                name='ck_shipping_rates_free_from_nonneg',
            ),
        ]

    def __str__(self):
        return f'{self.carrier} / {self.delivery_type}'
