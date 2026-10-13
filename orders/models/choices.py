from django.db import models


class OrderStatus(models.TextChoices):
    QUOTE_REQUESTED = 'QUOTE_REQUESTED', 'Waiting for admin pricing'
    NEW = 'NEW', 'New'
    PAID = 'PAID', 'Paid'
    PROCESSING = 'PROCESSING', 'Processing'
    SHIPPED = 'SHIPPED', 'Shipped'
    DELIVERED = 'DELIVERED', 'Delivered'
    CANCELLED = 'CANCELLED', 'Cancelled'
    REFUNDED = 'REFUNDED', 'Refunded'


class DeliveryType(models.TextChoices):
    PICKUP_POINT = 'PICKUP_POINT', 'Pickup point'
    HOME_DELIVERY = 'HOME_DELIVERY', 'Home delivery'


class PaymentMethod(models.TextChoices):
    CASH_ON_DELIVERY = 'CASH_ON_DELIVERY', 'Cash on delivery'
    BANK_TRANSFER = 'BANK_TRANSFER', 'Bank transfer'
    INVOICE = 'INVOICE', 'Invoice'


class PaymentStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    PAID = 'PAID', 'Paid'
    FAILED = 'FAILED', 'Failed'
    REFUNDED = 'REFUNDED', 'Refunded'


class ShipmentStatus(models.TextChoices):
    CREATED = 'CREATED', 'Created'
    LABEL_PRINTED = 'LABEL_PRINTED', 'Label printed'
    HANDED_OVER = 'HANDED_OVER', 'Handed to carrier'
    IN_TRANSIT = 'IN_TRANSIT', 'In transit'
    DELIVERED = 'DELIVERED', 'Delivered'
    RETURNED = 'RETURNED', 'Returned'


class Size(models.TextChoices):
    S = 'S', 'S'
    M = 'M', 'M'
    L = 'L', 'L'
    XL = 'XL', 'XL'
    XXL = 'XXL', 'XXL'
