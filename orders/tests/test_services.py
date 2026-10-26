import pytest
from rest_framework.exceptions import PermissionDenied

from catalog.models import PrintArea, ProductType
from core.exceptions import BusinessError, InvalidStatusTransitionError, OrderLockedError
from customers.models import Customer
from orders import services
from orders.models import DeliveryType, Order, OrderStatus, PaymentMethod, PaymentStatus
from shipping.models import ShippingPrice, ShippingQuantityDiscount

pytestmark = pytest.mark.django_db


def _billing():
    return {
        'billing_full_name': 'Jana Nováková',
        'billing_company_name': '',
        'billing_ico': '',
        'billing_dic': '',
        'billing_street': 'Horní 12',
        'billing_city': 'Vrchlabí',
        'billing_zip': '54301',
        'billing_country': 'CZ',
    }


def _shipping_same_as_billing():
    return {
        'shipping_full_name': 'Jana Nováková',
        'shipping_company_name': '',
        'shipping_street': 'Horní 12',
        'shipping_city': 'Vrchlabí',
        'shipping_zip': '54301',
        'shipping_country': 'CZ',
    }


def _no_delivery():
    return {'carrier': None, 'delivery_type': None, 'pickup_point_id': '', 'pickup_point_name': ''}


def _customer_data(email='jana@example.cz', marketing_consent=True):
    return {
        'email': email,
        'phone': '+420777123456',
        'first_name': 'Jana',
        'last_name': 'Nováková',
        'company_name': '',
        'marketing_consent': marketing_consent,
    }


def _make_order(items_data, payment_method=PaymentMethod.BANK_TRANSFER, email='jana@example.cz', delivery_data=None):
    return services.create_order(
        customer_data=_customer_data(email=email),
        billing_data=_billing(),
        shipping_data=_shipping_same_as_billing(),
        delivery_data=delivery_data if delivery_data is not None else _no_delivery(),
        payment_method=payment_method,
        note='',
        items_data=items_data,
    )


def test_catalog_item_uses_server_price_and_sets_new_status(motif, product_type, cut, color):
    items = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 2, 'placements': [],
    }]

    order = _make_order(items)

    assert order.status == OrderStatus.NEW
    item = order.items.get()
    # Server price wins regardless of whatever a client might have sent.
    assert item.unit_price == motif.price
    assert item.item_label == f'Bitcoin / {product_type.name} / {cut.name} / {color.name} / M'
    assert order.subtotal == motif.price * 2
    assert order.total is None  # shipping_cost not set yet


def test_custom_item_requires_quote_and_saves_placements(product_type, cut, color, print_area):
    items = [{
        'is_custom': True, 'motif': None, 'custom_text': 'Logo na záda', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'L', 'quantity': 15,
        'placements': [{
            'print_area': print_area, 'url': 'https://files.example/a.png',
            'x': 20, 'y': 30, 'width': 100, 'height': 100, 'rotation_deg': 0,
        }],
    }]

    order = _make_order(items)

    assert order.status == OrderStatus.QUOTE_REQUESTED
    assert order.subtotal is None
    assert order.total is None
    item = order.items.get()
    assert item.unit_price is None
    assert item.design_placements.count() == 1


def test_custom_item_without_placement_is_rejected(product_type, cut, color):
    items = [{
        'is_custom': True, 'motif': None, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'L', 'quantity': 1, 'placements': [],
    }]

    with pytest.raises(BusinessError):
        _make_order(items)

    # The whole transaction rolled back - no order, no customer either.
    assert Order.objects.count() == 0
    assert Customer.objects.count() == 0


def test_placement_print_area_must_match_product_type(product_type, cut, color):
    other_type = ProductType.objects.create(name='Mikina')
    mismatched_area = PrintArea.objects.create(product_type=other_type, name='Kapuce', width_mm=150, height_mm=150)

    items = [{
        'is_custom': True, 'motif': None, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'L', 'quantity': 1,
        'placements': [{
            'print_area': mismatched_area, 'url': 'x', 'x': 0, 'y': 0, 'width': 10, 'height': 10, 'rotation_deg': 0,
        }],
    }]

    with pytest.raises(BusinessError):
        _make_order(items)


def test_placement_out_of_bounds_is_rejected(product_type, cut, color, print_area):
    items = [{
        'is_custom': True, 'motif': None, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'L', 'quantity': 1,
        'placements': [{
            'print_area': print_area, 'url': 'x', 'x': 250, 'y': 0, 'width': 100, 'height': 100, 'rotation_deg': 0,
        }],
    }]

    with pytest.raises(BusinessError):
        _make_order(items)


def test_inactive_motif_is_rejected(motif, product_type, cut, color):
    motif.is_active = False
    motif.save()
    items = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 1, 'placements': [],
    }]

    with pytest.raises(BusinessError):
        _make_order(items)


def test_existing_email_different_case_reuses_customer_without_disabling_consent(motif, product_type, cut, color):
    items = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 1, 'placements': [],
    }]

    order1 = _make_order(items, email='Jana@Example.cz')
    order2 = services.create_order(
        customer_data=_customer_data(email='jana@example.cz', marketing_consent=False),
        billing_data=_billing(), shipping_data=_shipping_same_as_billing(), delivery_data=_no_delivery(),
        payment_method=PaymentMethod.BANK_TRANSFER, note='', items_data=items,
    )

    assert order1.customer_id == order2.customer_id
    assert Customer.objects.count() == 1
    order2.customer.refresh_from_db()
    assert order2.customer.marketing_consent is True  # never auto-flipped back to False


def test_pricing_and_shipping_completes_total_and_confirm_advances_status(product_type, cut, color, print_area):
    items = [{
        'is_custom': True, 'motif': None, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'L', 'quantity': 3,
        'placements': [{
            'print_area': print_area, 'url': 'x', 'x': 0, 'y': 0, 'width': 100, 'height': 100, 'rotation_deg': 0,
        }],
    }]
    order = _make_order(items)
    assert order.status == OrderStatus.QUOTE_REQUESTED

    item = order.items.get()
    services.update_item_pricing(order, item, unit_price=29900)
    order.refresh_from_db()
    assert order.subtotal == 29900 * 3
    assert order.total is None  # shipping still missing

    services.set_shipping_cost(order, 9900)
    order.refresh_from_db()
    assert order.total == 29900 * 3 + 9900

    services.confirm_order(order)
    order.refresh_from_db()
    assert order.status == OrderStatus.NEW
    assert order.confirmed_at is not None

    services.transition_order_status(order, OrderStatus.PROCESSING, is_admin=False)
    order.refresh_from_db()
    assert order.status == OrderStatus.PROCESSING


def test_invalid_status_transition_is_rejected(motif, product_type, cut, color):
    items = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 1, 'placements': [],
    }]
    order = _make_order(items)
    assert order.status == OrderStatus.NEW

    with pytest.raises(InvalidStatusTransitionError):
        services.transition_order_status(order, OrderStatus.DELIVERED, is_admin=True)


def test_staff_cannot_refund_only_admin_can(motif, product_type, cut, color):
    items = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 1, 'placements': [],
    }]
    order = _make_order(items)
    services.set_shipping_cost(order, 0)
    order.refresh_from_db()
    services.transition_order_status(order, OrderStatus.PAID, is_admin=False)
    order.refresh_from_db()

    with pytest.raises(PermissionDenied):
        services.transition_order_status(order, OrderStatus.REFUNDED, is_admin=False)

    services.transition_order_status(order, OrderStatus.REFUNDED, is_admin=True)
    order.refresh_from_db()
    assert order.status == OrderStatus.REFUNDED
    assert order.payment_status == PaymentStatus.REFUNDED


def test_editing_price_in_locked_state_raises_order_locked(motif, product_type, cut, color):
    items = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 1, 'placements': [],
    }]
    order = _make_order(items)
    services.set_shipping_cost(order, 0)
    order.refresh_from_db()
    services.transition_order_status(order, OrderStatus.PAID, is_admin=False)
    order.refresh_from_db()

    item = order.items.get()
    with pytest.raises(OrderLockedError):
        services.update_item_pricing(order, item, unit_price=1)


def test_price_change_on_confirmed_order_resets_confirmed_at(product_type, cut, color, print_area):
    items = [{
        'is_custom': True, 'motif': None, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'L', 'quantity': 1,
        'placements': [{
            'print_area': print_area, 'url': 'x', 'x': 0, 'y': 0, 'width': 100, 'height': 100, 'rotation_deg': 0,
        }],
    }]
    order = _make_order(items)
    item = order.items.get()
    services.update_item_pricing(order, item, unit_price=10000)
    services.set_shipping_cost(order, 0)
    order.refresh_from_db()
    services.confirm_order(order)
    order.refresh_from_db()
    assert order.confirmed_at is not None

    services.update_item_pricing(order, item, unit_price=12000)
    order.refresh_from_db()
    assert order.confirmed_at is None

    services.confirm_order(order)
    order.refresh_from_db()
    assert order.confirmed_at is not None

    services.set_shipping_cost(order, 500)
    order.refresh_from_db()
    assert order.confirmed_at is None


# ---------------------------------------------------------------------------
# Automatic shipping cost (price per carrier + delivery type, quantity
# discount tiers scoped to that pair).
# ---------------------------------------------------------------------------

def _home_delivery(carrier):
    return {'carrier': carrier, 'delivery_type': DeliveryType.HOME_DELIVERY, 'pickup_point_id': '', 'pickup_point_name': ''}


def _personal_pickup():
    return {'carrier': None, 'delivery_type': DeliveryType.PERSONAL_PICKUP, 'pickup_point_id': '', 'pickup_point_name': ''}


def test_shipping_cost_stays_null_without_a_configured_price_for_the_pair(motif, product_type, cut, color, carrier):
    items = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 2, 'placements': [],
    }]

    order = _make_order(items, delivery_data=_home_delivery(carrier))

    assert order.shipping_cost is None
    assert order.total is None


def test_automatic_shipping_cost_uses_price_for_the_carrier_and_delivery_type(motif, product_type, cut, color, carrier):
    ShippingPrice.objects.create(carrier=carrier, delivery_type=DeliveryType.HOME_DELIVERY, price=5000)
    items = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 2, 'placements': [],
    }]

    order = _make_order(items, delivery_data=_home_delivery(carrier))

    assert order.shipping_cost == 5000
    assert order.total == motif.price * 2 + 5000


def test_automatic_shipping_cost_applies_best_matching_quantity_discount(motif, product_type, cut, color, carrier):
    shipping_price = ShippingPrice.objects.create(carrier=carrier, delivery_type=DeliveryType.HOME_DELIVERY, price=5000)
    ShippingQuantityDiscount.objects.create(shipping_price=shipping_price, min_quantity=30, discount_percent=30)
    ShippingQuantityDiscount.objects.create(shipping_price=shipping_price, min_quantity=50, discount_percent=100)

    items_30 = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 30, 'placements': [],
    }]
    order_30 = _make_order(items_30, email='thirty@example.cz', delivery_data=_home_delivery(carrier))
    assert order_30.shipping_cost == 3500  # 5000 * (1 - 0.30)

    items_50 = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 50, 'placements': [],
    }]
    order_50 = _make_order(items_50, email='fifty@example.cz', delivery_data=_home_delivery(carrier))
    assert order_50.shipping_cost == 0  # 100% discount from the 50+ tier


def test_automatic_shipping_cost_ignores_inactive_discount_tier(motif, product_type, cut, color, carrier):
    shipping_price = ShippingPrice.objects.create(carrier=carrier, delivery_type=DeliveryType.HOME_DELIVERY, price=5000)
    ShippingQuantityDiscount.objects.create(
        shipping_price=shipping_price, min_quantity=30, discount_percent=100, is_active=False,
    )

    items = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 30, 'placements': [],
    }]
    order = _make_order(items, delivery_data=_home_delivery(carrier))

    assert order.shipping_cost == 5000  # the only tier is inactive, so no discount applies


def test_automatic_shipping_cost_applies_even_while_items_await_a_quote(product_type, cut, color, print_area, carrier):
    """Shipping only depends on total item quantity, never on unit price -
    it's priced automatically even for a custom item still awaiting an
    admin quote, while `total` itself stays NULL until the item is priced.
    """
    ShippingPrice.objects.create(carrier=carrier, delivery_type=DeliveryType.HOME_DELIVERY, price=5000)
    items = [{
        'is_custom': True, 'motif': None, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'L', 'quantity': 3,
        'placements': [{
            'print_area': print_area, 'url': 'x', 'x': 0, 'y': 0, 'width': 100, 'height': 100, 'rotation_deg': 0,
        }],
    }]

    order = _make_order(items, delivery_data=_home_delivery(carrier))

    assert order.status == OrderStatus.QUOTE_REQUESTED
    assert order.shipping_cost == 5000
    assert order.subtotal is None
    assert order.total is None  # still unknown - the item itself has no price yet


def test_personal_pickup_is_always_free_without_any_configured_price(motif, product_type, cut, color):
    """PERSONAL_PICKUP never touches ShippingPrice at all - free regardless
    of whether any carrier/delivery-type price has been configured.
    """
    items = [{
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 1, 'placements': [],
    }]

    order = _make_order(items, delivery_data=_personal_pickup())

    assert order.shipping_cost == 0
    assert order.total == motif.price
