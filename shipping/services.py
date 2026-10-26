from .models import ShippingPrice, ShippingQuantityDiscount


def calculate_shipping_cost(*, total_quantity):
    """The shop has one shipping price with quantity discount tiers on top
    (e.g. 30+ pieces -30%, 50+ pieces free). Returns the price in haléře
    after the best matching active discount, or None if admin hasn't set a
    base price yet - callers must treat None as "unknown, admin prices it
    manually", not as free shipping.
    """
    base_price = ShippingPrice.objects.filter(pk=1).values_list('price', flat=True).first()
    if base_price is None:
        return None

    tier = ShippingQuantityDiscount.objects.filter(
        is_active=True, min_quantity__lte=total_quantity,
    ).order_by('-min_quantity').first()
    discount_percent = tier.discount_percent if tier else 0

    return base_price * (100 - discount_percent) // 100