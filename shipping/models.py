from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import SimpleLookup, UUIDModel


class Carrier(SimpleLookup):
    """Delivery carrier. No capability flags - pricing lives on ShippingPrice
    below, one row per (carrier, delivery_type); admin still sets
    Order.shipping_cost manually whenever automatic pricing doesn't apply.
    """
    code = models.CharField(max_length=50, unique=True, help_text='Internal code, e.g. PACKETA')

    class Meta(SimpleLookup.Meta):
        db_table = 'carriers'
        verbose_name = _('carrier')
        verbose_name_plural = _('carriers')


# Mirrors orders.models.DeliveryType's PICKUP_POINT/HOME_DELIVERY values
# (not imported directly - shipping/models.py must not import orders.models,
# orders.models already imports Carrier from here, and a model-layer import
# in both directions would deadlock Django's app loading). PERSONAL_PICKUP
# deliberately has no ShippingPrice row at all - it's always free, handled
# in shipping.services.calculate_shipping_cost before any DB lookup.
SHIPPING_PRICE_DELIVERY_TYPE_CHOICES = [
    ('PICKUP_POINT', 'Pickup point'),
    ('HOME_DELIVERY', 'Home delivery'),
]


class ShippingPrice(UUIDModel):
    """One shipping price per (carrier, delivery_type), in haléře, before
    any quantity discount from ShippingQuantityDiscount. No row for a given
    pair means admin hasn't configured it yet - automatic shipping pricing
    is then skipped for that pair and admin keeps pricing shipping manually,
    same as before this feature existed.
    """
    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, related_name='shipping_prices')
    delivery_type = models.CharField(max_length=20, choices=SHIPPING_PRICE_DELIVERY_TYPE_CHOICES)
    price = models.PositiveIntegerField(help_text='In haléře.')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shipping_prices'
        unique_together = [('carrier', 'delivery_type')]
        verbose_name = _('shipping price')
        verbose_name_plural = _('shipping prices')

    def __str__(self):
        return f'{self.carrier.name} / {self.get_delivery_type_display()}'


class PacketaConfig(models.Model):
    """Singleton: credentials and settings for the Packeta (Zásilkovna)
    carrier API (see shipping/packeta_client.py) - set by admin through
    GET/PUT /admin/packeta-config, not env vars, so it can be changed
    without a deploy. `api_password = ''` means Packeta integration isn't
    configured yet - shipment creation then fails with a clear error
    instead of calling the API with an empty password.
    """
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    api_password = models.CharField(max_length=100, blank=True, default='')
    eshop = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Sender label - only needed if the Packeta account has multiple senders.',
    )
    home_delivery_address_id = models.CharField(
        max_length=50, blank=True, default='',
        help_text="ID of the external carrier used for home delivery (not a pickup point ID) - "
                  "from Packeta's client zone / address delivery carriers feed.",
    )
    label_format = models.CharField(max_length=50, default='A6 on A4')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'packeta_config'
        verbose_name = _('Packeta configuration')
        verbose_name_plural = _('Packeta configuration')

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'Packeta'


class CodSurcharge(models.Model):
    """Singleton: flat surcharge (haléře) added to shipping_cost whenever
    Order.payment_method is CASH_ON_DELIVERY - the carrier charges the shop
    a fee for collecting cash on delivery, optionally passed on to the
    customer. Never applies to PERSONAL_PICKUP (always free, no carrier
    involved - there's no cash collection fee to pass on).
    """
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    amount = models.PositiveIntegerField(default=0, help_text='In haléře; 0 = no surcharge.')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cod_surcharge'
        verbose_name = _('COD surcharge')
        verbose_name_plural = _('COD surcharge')

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'Příplatek za dobírku'


class ShippingQuantityDiscount(UUIDModel):
    """Quantity-based discount tier on top of one ShippingPrice row, e.g.
    'from 30 pieces, 30% off; from 50 pieces, free'. Scoped to a single
    (carrier, delivery_type) pair, not shop-wide - each pair has its own
    tiers. For a given order, the tier with the highest min_quantity at or
    below the order's total item quantity (summed across all order_items,
    regardless of unit price) wins; if none match, the full
    ShippingPrice.price applies.
    """
    shipping_price = models.ForeignKey(ShippingPrice, on_delete=models.CASCADE, related_name='quantity_discounts')
    min_quantity = models.PositiveIntegerField(help_text='Total item quantity in the order, inclusive.')
    discount_percent = models.PositiveIntegerField(help_text='0-100; 100 means free shipping from this quantity.')
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shipping_quantity_discounts'
        ordering = ['min_quantity']
        unique_together = [('shipping_price', 'min_quantity')]
        verbose_name = _('shipping quantity discount')
        verbose_name_plural = _('shipping quantity discounts')
        constraints = [
            models.CheckConstraint(condition=models.Q(min_quantity__gte=1), name='shipping_discount_min_quantity_positive'),
            models.CheckConstraint(
                condition=models.Q(discount_percent__gte=0) & models.Q(discount_percent__lte=100),
                name='shipping_discount_percent_range',
            ),
        ]

    def __str__(self):
        return f'{self.min_quantity}+ ks → -{self.discount_percent}%'