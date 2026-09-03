from django.conf import settings as django_settings
from django.db import models

from catalog.models import ProductVariant
from core.models import TimestampedModel, UUIDModel
from customers.models import Address, Customer
from shipping.models import Carrier, DeliveryType


class Order(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        NEW = 'NEW', 'Nová'
        PAID = 'PAID', 'Zaplacená'
        PROCESSING = 'PROCESSING', 'Zpracovává se'
        SHIPPED = 'SHIPPED', 'Odeslaná'
        DELIVERED = 'DELIVERED', 'Doručená'
        CANCELLED = 'CANCELLED', 'Zrušená'
        REFUNDED = 'REFUNDED', 'Vrácená'

    order_number = models.TextField(unique=True)
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='orders', null=True, blank=True,
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.NEW)
    subtotal = models.IntegerField(help_text='Cena v haléřích')
    shipping_cost = models.IntegerField(help_text='Cena v haléřích')
    total = models.IntegerField(help_text='Cena v haléřích')
    currency = models.TextField(default='CZK')
    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, related_name='orders')
    delivery_type = models.CharField(max_length=15, choices=DeliveryType.choices)
    pickup_point_id = models.TextField(null=True, blank=True)
    pickup_point_name = models.TextField(null=True, blank=True)
    billing_address = models.ForeignKey(
        Address, on_delete=models.PROTECT, related_name='billing_orders', null=True, blank=True,
    )
    shipping_address = models.ForeignKey(
        Address, on_delete=models.PROTECT, related_name='shipping_orders', null=True, blank=True,
    )
    note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'orders'
        constraints = [
            models.CheckConstraint(check=models.Q(subtotal__gte=0), name='ck_orders_subtotal_nonneg'),
            models.CheckConstraint(check=models.Q(shipping_cost__gte=0), name='ck_orders_shipping_cost_nonneg'),
            models.CheckConstraint(check=models.Q(total__gte=0), name='ck_orders_total_nonneg'),
        ]

    def __str__(self):
        return self.order_number


class OrderStatusHistory(UUIDModel):
    """Historie změn stavu objednávky — pro audit a zákaznický servis."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='status_history')
    status = models.CharField(max_length=15, choices=Order.Status.choices)
    note = models.TextField(null=True, blank=True)
    changed_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='order_status_changes',
        null=True, blank=True, help_text='NULL = změna systémem (např. webhook platby)',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'order_status_history'
        verbose_name_plural = 'order status history'

    def __str__(self):
        return f'{self.order} -> {self.status}'


class OrderItem(UUIDModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.PROTECT, related_name='order_items',
    )
    product_name = models.TextField(help_text='Snapshot v čase objednání')
    variant_label = models.TextField(help_text='Např. "Pánské / černá / M"')
    unit_price = models.IntegerField(help_text='Cena v haléřích')
    quantity = models.IntegerField()

    class Meta:
        db_table = 'order_items'
        constraints = [
            models.CheckConstraint(check=models.Q(unit_price__gte=0), name='ck_order_items_unit_price_nonneg'),
            models.CheckConstraint(check=models.Q(quantity__gt=0), name='ck_order_items_quantity_positive'),
        ]

    def __str__(self):
        return f'{self.product_name} x{self.quantity}'
