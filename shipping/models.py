from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import SimpleLookup, UUIDModel


class Carrier(SimpleLookup):
    """Delivery carrier. No pricing/capability flags - the shop has a
    single shipping price (see ShippingPrice below), independent of which
    carrier is chosen; admin still sets Order.shipping_cost manually
    whenever automatic pricing doesn't apply.
    """
    code = models.CharField(max_length=50, unique=True, help_text='Internal code, e.g. PACKETA')

    class Meta(SimpleLookup.Meta):
        db_table = 'carriers'
        verbose_name = _('carrier')
        verbose_name_plural = _('carriers')


class ShippingPrice(models.Model):
    """Singleton: the shop's one shipping price, in haléře, before any
    quantity discount from ShippingQuantityDiscount. Always exactly one
    row (pk=1). `price = NULL` means admin hasn't configured it yet -
    automatic shipping pricing is then skipped and admin keeps pricing
    shipping manually per order, same as before this feature existed.
    """
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    price = models.PositiveIntegerField(null=True, blank=True, help_text='In haléře; NULL = not configured yet.')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shipping_price'
        verbose_name = _('shipping price')
        verbose_name_plural = _('shipping price')

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'Cena dopravy'


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


class ShippingQuantityDiscount(UUIDModel):
    """Quantity-based discount tier on top of ShippingPrice.price, e.g.
    'from 30 pieces, 30% off; from 50 pieces, free'. For a given order, the
    tier with the highest min_quantity at or below the order's total item
    quantity (summed across all order_items, regardless of unit price)
    wins; if none match, the full ShippingPrice.price applies.
    """
    min_quantity = models.PositiveIntegerField(unique=True, help_text='Total item quantity in the order, inclusive.')
    discount_percent = models.PositiveIntegerField(help_text='0-100; 100 means free shipping from this quantity.')
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shipping_quantity_discounts'
        ordering = ['min_quantity']
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