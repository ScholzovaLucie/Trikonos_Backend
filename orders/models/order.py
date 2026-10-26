from django.db import models

from core.models import UUIDModel
from customers.models import Customer
from shipping.models import Carrier
from .choices import (
    DeliveryType,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    ShipmentStatus,
)


class Order(UUIDModel):
    """Carries identity, pricing, shipping, payment and both addresses in a
    single row - a deliberate tradeoff for simplicity (no joins needed to
    render an order).
    """
    order_number = models.CharField(max_length=50, unique=True, help_text='Generated from order_number_seq')
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='orders')
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.NEW, db_index=True)

    # Pricing - nullable until set (custom print waits on admin pricing)
    subtotal = models.PositiveIntegerField(null=True, blank=True, help_text='In haléře')
    shipping_cost = models.PositiveIntegerField(null=True, blank=True)
    shipping_cost_manual = models.BooleanField(
        default=False,
        help_text='True once admin overrides shipping_cost by hand - stops it from being '
                  'recalculated automatically (e.g. on a quantity change) until reset.',
    )
    total = models.PositiveIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default='CZK')

    # Shipping
    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, null=True, blank=True, related_name='orders')
    delivery_type = models.CharField(max_length=20, choices=DeliveryType.choices, null=True, blank=True)
    pickup_point_id = models.CharField(max_length=100, blank=True, default='')
    pickup_point_name = models.CharField(max_length=255, blank=True, default='')
    tracking_number = models.CharField(max_length=100, blank=True, default='')
    carrier_shipment_id = models.CharField(max_length=50, blank=True, default='', help_text="Packeta's own packet ID")
    shipment_status = models.CharField(max_length=20, choices=ShipmentStatus.choices, default=ShipmentStatus.CREATED)

    # Payment
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, null=True, blank=True)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)

    # Billing address
    billing_full_name = models.CharField(max_length=255)
    billing_company_name = models.CharField(max_length=255, blank=True, default='')
    billing_ico = models.CharField(max_length=20, blank=True, default='')
    billing_dic = models.CharField(max_length=20, blank=True, default='')
    billing_street = models.CharField(max_length=255)
    billing_city = models.CharField(max_length=255)
    billing_zip = models.CharField(max_length=20)
    billing_country = models.CharField(max_length=2, default='CZ')

    # Shipping address (app pre-fills with billing values when identical)
    shipping_full_name = models.CharField(max_length=255)
    shipping_company_name = models.CharField(max_length=255, blank=True, default='')
    shipping_street = models.CharField(max_length=255)
    shipping_city = models.CharField(max_length=255)
    shipping_zip = models.CharField(max_length=20)
    shipping_country = models.CharField(max_length=2, default='CZ')

    note = models.TextField(blank=True, default='')
    # Set the first time POST /orders/{id}/confirm succeeds - gates the
    # NEW -> PROCESSING transition (the customer must have seen the final
    # price/payment instructions before admin can start processing).
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'orders'
        constraints = [
            models.CheckConstraint(condition=models.Q(subtotal__isnull=True) | models.Q(subtotal__gte=0), name='order_subtotal_non_negative'),
            models.CheckConstraint(condition=models.Q(shipping_cost__isnull=True) | models.Q(shipping_cost__gte=0), name='order_shipping_cost_non_negative'),
            models.CheckConstraint(condition=models.Q(total__isnull=True) | models.Q(total__gte=0), name='order_total_non_negative'),
        ]

    def __str__(self):
        return self.order_number
