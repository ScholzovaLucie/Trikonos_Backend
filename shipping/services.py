from core.exceptions import BusinessError

from .models import PacketaConfig, ShippingPrice, ShippingQuantityDiscount


def calculate_shipping_cost(*, carrier, delivery_type, total_quantity):
    """Price depends on which carrier + delivery type was chosen, each with
    its own quantity discount tiers on top (e.g. 30+ pieces -30%, 50+ pieces
    free). Personal pickup at the shop is always free, no carrier or price
    row involved. Returns the price in haléře after the best matching
    active discount, or None if either delivery hasn't been chosen yet
    (`carrier` is None) or admin hasn't configured a price for this pair -
    callers must treat None as "unknown", not as free shipping.
    """
    from orders.models import DeliveryType

    if delivery_type == DeliveryType.PERSONAL_PICKUP:
        return 0
    if carrier is None:
        return None

    price_row = ShippingPrice.objects.filter(carrier=carrier, delivery_type=delivery_type).first()
    if price_row is None:
        return None

    tier = price_row.quantity_discounts.filter(
        is_active=True, min_quantity__lte=total_quantity,
    ).order_by('-min_quantity').first()
    discount_percent = tier.discount_percent if tier else 0

    return price_row.price * (100 - discount_percent) // 100


# ---------------------------------------------------------------------------
# Packeta packet creation - maps an Order onto Packeta's PacketAttributes.
# ---------------------------------------------------------------------------

def _split_street(street):
    """Packeta wants street and house number as separate fields; the shop
    only stores one combined `street` string. Best-effort split on the
    last whitespace-separated token (house numbers are always last in
    Czech addresses) - if that token isn't number-like, the whole address
    goes into `street` and `houseNumber` is left blank rather than guessing
    wrong.
    """
    parts = street.rsplit(' ', 1)
    if len(parts) == 2 and any(char.isdigit() for char in parts[1]):
        return parts[0], parts[1]
    return street, ''


def build_packet_attributes(order, *, weight, value=None):
    """Maps an Order onto Packeta's PacketAttributes for createPacket - the
    same method creates a packet for any delivery type, distinguished only
    by which fields are set here. Raises BusinessError if the order isn't
    ready for shipment creation (no carrier/delivery type) or home delivery
    isn't configured.
    """
    from orders.models import DeliveryType, PaymentMethod

    packeta_config = PacketaConfig.get_solo()

    if order.carrier is None or order.carrier.code != 'PACKETA':
        raise BusinessError('Zásilku lze přes toto rozhraní vytvořit jen pro dopravce Packeta.')
    if not order.delivery_type:
        raise BusinessError('Objednávka nemá nastavený typ doručení.')
    resolved_value = value if value is not None else order.total
    if resolved_value is None:
        raise BusinessError('Objednávka nemá stanovenou celkovou cenu - zadej pojistnou hodnotu (value) ručně.')

    attributes = {
        'number': order.order_number,
        'name': order.customer.first_name,
        'surname': order.customer.last_name,
        'email': order.customer.email,
        'phone': order.customer.phone,
        'currency': order.currency,
        'value': resolved_value / 100,
        'weight': weight,
    }
    if order.customer.company_name:
        attributes['company'] = order.customer.company_name
    if packeta_config.eshop:
        attributes['eshop'] = packeta_config.eshop
    if order.payment_method == PaymentMethod.CASH_ON_DELIVERY:
        attributes['cod'] = resolved_value / 100

    if order.delivery_type == DeliveryType.PICKUP_POINT:
        attributes['addressId'] = order.pickup_point_id
    else:
        home_delivery_address_id = packeta_config.home_delivery_address_id
        if not home_delivery_address_id:
            raise BusinessError(
                'Doručení na adresu u Packety není nakonfigurované - nastav homeDeliveryAddressId v '
                '/admin/packeta-config.',
            )
        street, house_number = _split_street(order.shipping_street)
        attributes.update({
            'addressId': home_delivery_address_id,
            'street': street,
            'houseNumber': house_number,
            'city': order.shipping_city,
            'zip': order.shipping_zip,
        })

    return attributes