"""Order business logic. Views/serializers only shape request/response data -
every price, label, status transition and validation rule lives here, per
the API guide's "server never trusts the frontend" rule.
"""
from dataclasses import dataclass
from typing import Callable, Optional

from django.db import connection, transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from rest_framework import exceptions as drf_exceptions

from core.exceptions import BusinessError, InvalidStatusTransitionError, OrderLockedError

from .models import (
    CustomDesignPlacement,
    Order,
    OrderItem,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    ShipmentStatus,
)

MAX_ITEM_QUANTITY = 500
EDITABLE_STATUSES = {OrderStatus.QUOTE_REQUESTED, OrderStatus.NEW}


# ---------------------------------------------------------------------------
# Order number - TRK-<year>-<nextval>. Backed by a plain Postgres SEQUENCE,
# not a Django model (there's no ORM concept for a bare sequence), so it's
# provisioned lazily here rather than through a migration.
# ---------------------------------------------------------------------------

def generate_order_number():
    # The sequence itself is provisioned by migrations/0002_order_number_seq.py,
    # not here - a sequence has no Django ORM representation, so that
    # migration is the one place that creates/drops it.
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('order_number_seq')")
        value = cursor.fetchone()[0]
    return f'TRK-{timezone.now().year}-{value:04d}'


# ---------------------------------------------------------------------------
# Customer dedup
# ---------------------------------------------------------------------------

def get_or_create_customer(data):
    """Find by case-insensitive e-mail (stored lowercase) or create. On an
    existing match, refresh contact details from this order but never flip
    marketing_consent back to False automatically - only an explicit True
    does that; opting back out is an admin-only action.
    """
    from customers.models import Customer

    email = data['email'].strip().lower()
    customer = Customer.objects.filter(email__iexact=email).first()

    if customer is None:
        return Customer.objects.create(
            email=email,
            phone=data.get('phone', ''),
            first_name=data.get('first_name', ''),
            last_name=data.get('last_name', ''),
            company_name=data.get('company_name') or '',
            marketing_consent=bool(data.get('marketing_consent', False)),
        )

    customer.email = email
    customer.phone = data.get('phone', customer.phone)
    customer.first_name = data.get('first_name', customer.first_name)
    customer.last_name = data.get('last_name', customer.last_name)
    customer.company_name = data.get('company_name') or customer.company_name
    if data.get('marketing_consent'):
        customer.marketing_consent = True
    customer.save()
    return customer


# ---------------------------------------------------------------------------
# Item validation, labels, pricing
# ---------------------------------------------------------------------------

def _require_active(instance, label):
    if not instance.is_active:
        raise BusinessError(_('%(label)s is not active.') % {'label': label}, code='INACTIVE_REFERENCE')


def build_item_label(is_custom, motif, product_type, cut, color, size):
    first = _('Custom print') if is_custom else motif.name
    return f'{first} / {product_type.name} / {cut.name} / {color.name} / {size}'


def validate_placement_bounds(print_area, x, y, width, height):
    if width <= 0 or height <= 0:
        raise BusinessError(_('Placement dimensions must be positive.'), code='INVALID_PLACEMENT_SIZE')
    if x < 0 or y < 0:
        raise BusinessError(_('Placement must not start outside the print area.'), code='INVALID_PLACEMENT_POSITION')
    if x + width > print_area.width_mm or y + height > print_area.height_mm:
        raise BusinessError(_('Placement exceeds the print area dimensions.'), code='PLACEMENT_OUT_OF_BOUNDS')


def _validate_and_price_item(item_data):
    """item_data keys (already resolved to model instances by the caller):
    is_custom, motif, custom_text, product_type, cut, color, size, quantity,
    placements (list of dicts: print_area, url, x, y, width, height,
    rotation_deg). Raises BusinessError (422) on any rule violation, returns
    (item_label, unit_price) on success. Pure - does not touch the database.
    """
    is_custom = item_data['is_custom']
    motif = item_data.get('motif')
    product_type = item_data['product_type']
    cut = item_data['cut']
    color = item_data['color']
    size = item_data['size']
    quantity = item_data['quantity']
    placements = item_data.get('placements') or []

    if not (1 <= quantity <= MAX_ITEM_QUANTITY):
        raise BusinessError(
            _('Quantity must be between 1 and %(max)s.') % {'max': MAX_ITEM_QUANTITY},
            code='QUANTITY_OUT_OF_RANGE',
        )

    if is_custom:
        if motif is not None:
            raise BusinessError(_('A custom item cannot have a catalog motif assigned.'), code='CUSTOM_WITH_MOTIF')
        if not placements:
            raise BusinessError(
                _('A custom item must have at least one design placement.'), code='CUSTOM_WITHOUT_PLACEMENT',
            )
    else:
        if motif is None:
            raise BusinessError(_('A catalog item must have a motif assigned.'), code='CATALOG_WITHOUT_MOTIF')
        _require_active(motif, _('Motif'))
        if placements:
            raise BusinessError(_('A catalog item cannot have design placements.'), code='CATALOG_WITH_PLACEMENT')

    _require_active(product_type, _('Product type'))
    _require_active(cut, _('Cut'))
    _require_active(color, _('Color'))

    for placement in placements:
        print_area = placement['print_area']
        if print_area.product_type_id != product_type.id:
            raise BusinessError(
                _('The print area belongs to a different product type than the item.'),
                code='PRINT_AREA_PRODUCT_TYPE_MISMATCH',
            )
        validate_placement_bounds(
            print_area, placement.get('x', 0), placement.get('y', 0), placement['width'], placement['height'],
        )

    # Admin manual order creation may pass an explicit unit_price (e.g. to
    # pre-fill a quote); public checkout never sets this key, so it falls
    # back to the catalog price (or None for a custom item awaiting a quote).
    unit_price = item_data.get('unit_price')
    if unit_price is None:
        unit_price = motif.price if not is_custom else None
    item_label = build_item_label(is_custom, motif, product_type, cut, color, size)
    return item_label, unit_price


def build_order_item(order, item_data):
    """Validates, prices and persists one order item + its placements."""
    item_label, unit_price = _validate_and_price_item(item_data)
    placements = item_data.get('placements') or []

    item = OrderItem.objects.create(
        order=order,
        is_custom=item_data['is_custom'],
        motif=item_data.get('motif'),
        custom_text=item_data.get('custom_text', ''),
        product_type=item_data['product_type'],
        cut=item_data['cut'],
        color=item_data['color'],
        size=item_data['size'],
        item_label=item_label,
        unit_price=unit_price,
        quantity=item_data['quantity'],
    )

    for placement in placements:
        CustomDesignPlacement.objects.create(
            order_item=item,
            print_area=placement['print_area'],
            url=placement['url'],
            x=placement.get('x', 0),
            y=placement.get('y', 0),
            width=placement['width'],
            height=placement['height'],
            rotation_deg=placement.get('rotation_deg', 0),
        )

    return item


def validate_cart_item(item_data):
    """Non-raising variant for POST /cart/validate - the cart UI needs a
    per-line verdict instead of the request aborting on the first bad item.
    """
    try:
        item_label, unit_price = _validate_and_price_item(item_data)
    except BusinessError as exc:
        return {'valid': False, 'errors': [str(exc.detail)], 'item_label': None, 'unit_price': None, 'line_total': None}

    quantity = item_data['quantity']
    line_total = unit_price * quantity if unit_price is not None else None
    return {'valid': True, 'errors': [], 'item_label': item_label, 'unit_price': unit_price, 'line_total': line_total}


# ---------------------------------------------------------------------------
# Totals - recomputed after every price/quantity/shipping change.
# ---------------------------------------------------------------------------

def recalculate_totals(order):
    items = list(order.items.all())
    if items and all(item.unit_price is not None for item in items):
        subtotal = sum(item.unit_price * item.quantity for item in items)
    else:
        subtotal = None

    order.subtotal = subtotal
    order.total = subtotal + order.shipping_cost if subtotal is not None and order.shipping_cost is not None else None
    order.save(update_fields=['subtotal', 'total', 'updated_at'])
    return order


# ---------------------------------------------------------------------------
# Order creation - customer + order + items + placements, one transaction.
# ---------------------------------------------------------------------------

@transaction.atomic
def create_order(*, customer_data, billing_data, shipping_data, delivery_data, payment_method, note, items_data):
    customer = get_or_create_customer(customer_data)

    has_custom_item = any(item['is_custom'] for item in items_data)
    status = OrderStatus.QUOTE_REQUESTED if has_custom_item else OrderStatus.NEW

    order = Order.objects.create(
        order_number=generate_order_number(),
        customer=customer,
        status=status,
        currency='CZK',
        payment_method=payment_method,
        note=note or '',
        **billing_data,
        **shipping_data,
        **delivery_data,
    )

    for item_data in items_data:
        build_order_item(order, item_data)

    recalculate_totals(order)

    from .emails import send_order_received_email, send_shop_notification_email
    transaction.on_commit(lambda: send_order_received_email(order))
    transaction.on_commit(lambda: send_shop_notification_email(order))

    return order


# ---------------------------------------------------------------------------
# Lock rule - prices/shipping/addresses only change in QUOTE_REQUESTED/NEW.
# ---------------------------------------------------------------------------

def ensure_order_editable(order):
    if order.status not in EDITABLE_STATUSES:
        raise OrderLockedError()


UNSET = object()


def _unconfirm_if_confirmed(order):
    """A price/quantity/shipping change on an already-confirmed NEW order
    invalidates that confirmation - the customer saw a price that no longer
    holds, so admin must call /confirm again before NEW -> PROCESSING.
    """
    if order.confirmed_at is not None:
        order.confirmed_at = None
        order.save(update_fields=['confirmed_at', 'updated_at'])


def update_item_pricing(order, item, *, unit_price=UNSET, quantity=UNSET):
    """unit_price/quantity default to the UNSET sentinel (not None) so a
    caller can explicitly set unit_price=None to revert an item back to
    "awaiting quote" without that being indistinguishable from "not passed".
    """
    ensure_order_editable(order)
    if unit_price is not UNSET:
        item.unit_price = unit_price
    if quantity is not UNSET:
        if not (1 <= quantity <= MAX_ITEM_QUANTITY):
            raise BusinessError(
                _('Quantity must be between 1 and %(max)s.') % {'max': MAX_ITEM_QUANTITY},
                code='QUANTITY_OUT_OF_RANGE',
            )
        item.quantity = quantity
    item.save()
    recalculate_totals(order)
    _unconfirm_if_confirmed(order)
    return item


def set_shipping_cost(order, shipping_cost):
    ensure_order_editable(order)
    order.shipping_cost = shipping_cost
    order.save(update_fields=['shipping_cost', 'updated_at'])
    recalculate_totals(order)
    _unconfirm_if_confirmed(order)
    return order


def confirm_order(order):
    """POST /orders/{id}/confirm. Idempotent - calling it again just re-sends
    the e-mail (that part is wired in the view/task #11), confirmed_at only
    gets set once but is re-saved to keep `updated_at` honest.
    """
    if order.total is None:
        raise BusinessError(_('The order does not have a total price set.'), code='ORDER_NOT_PRICED')
    if order.status == OrderStatus.QUOTE_REQUESTED:
        order.status = OrderStatus.NEW
    order.confirmed_at = order.confirmed_at or timezone.now()
    order.save(update_fields=['status', 'confirmed_at', 'updated_at'])
    return order


# ---------------------------------------------------------------------------
# Status transition map (chapter 3 of the API guide) - one table, not
# scattered ifs. Each entry can carry a condition and an admin-only flag.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StatusTransition:
    source: str
    target: str
    admin_only: bool = False
    condition: Optional[Callable] = None
    condition_message: str = ''


def _all_items_priced(order):
    return all(item.unit_price is not None for item in order.items.all())


def _total_is_set(order):
    return order.total is not None


def _confirmed_and_priced(order):
    return order.total is not None and order.confirmed_at is not None


def _carrier_and_delivery_set(order):
    return order.carrier_id is not None and bool(order.delivery_type)


# condition_message values are built once at module import time, so they use
# gettext_lazy (resolved on access/str(), not at import time) rather than the
# eager gettext used everywhere else in this module.
TRANSITIONS = [
    StatusTransition(
        OrderStatus.QUOTE_REQUESTED, OrderStatus.NEW,
        condition=_all_items_priced, condition_message=gettext_lazy('All items must have a price set.'),
    ),
    StatusTransition(OrderStatus.QUOTE_REQUESTED, OrderStatus.CANCELLED),
    StatusTransition(OrderStatus.NEW, OrderStatus.CANCELLED),
    StatusTransition(
        OrderStatus.NEW, OrderStatus.PAID,
        condition=_total_is_set, condition_message=gettext_lazy('The total price must be set.'),
    ),
    StatusTransition(
        OrderStatus.NEW, OrderStatus.PROCESSING,
        condition=_confirmed_and_priced,
        condition_message=gettext_lazy('The order must have a total price and must be confirmed by an admin.'),
    ),
    StatusTransition(OrderStatus.PAID, OrderStatus.PROCESSING),
    StatusTransition(
        OrderStatus.PROCESSING, OrderStatus.SHIPPED,
        condition=_carrier_and_delivery_set, condition_message=gettext_lazy('A carrier and delivery type must be set.'),
    ),
    StatusTransition(OrderStatus.PROCESSING, OrderStatus.CANCELLED, admin_only=True),
    StatusTransition(OrderStatus.SHIPPED, OrderStatus.DELIVERED),
    StatusTransition(OrderStatus.PAID, OrderStatus.REFUNDED, admin_only=True),
    StatusTransition(OrderStatus.PROCESSING, OrderStatus.REFUNDED, admin_only=True),
    StatusTransition(OrderStatus.SHIPPED, OrderStatus.REFUNDED, admin_only=True),
    StatusTransition(OrderStatus.DELIVERED, OrderStatus.REFUNDED, admin_only=True),
]

_TRANSITIONS_BY_PAIR = {(t.source, t.target): t for t in TRANSITIONS}

_SHIPMENT_STATUS_ORDER = [
    ShipmentStatus.CREATED,
    ShipmentStatus.LABEL_PRINTED,
    ShipmentStatus.HANDED_OVER,
    ShipmentStatus.IN_TRANSIT,
    ShipmentStatus.DELIVERED,
    ShipmentStatus.RETURNED,
]


def _shipment_status_rank(value):
    try:
        return _SHIPMENT_STATUS_ORDER.index(value)
    except ValueError:
        return -1


def get_allowed_transitions(order, *, is_admin):
    """Target statuses reachable from the order's current status for this
    actor - drives the `allowedTransitions` field in the admin order detail.
    """
    targets = []
    for transition in TRANSITIONS:
        if transition.source != order.status:
            continue
        if transition.admin_only and not is_admin:
            continue
        if transition.condition and not transition.condition(order):
            continue
        targets.append(transition.target)
    return targets


def transition_order_status(order, target_status, *, is_admin):
    transition = _TRANSITIONS_BY_PAIR.get((order.status, target_status))
    if transition is None:
        raise InvalidStatusTransitionError(
            _('Transition from status %(source)s to %(target)s is not allowed.')
            % {'source': order.status, 'target': target_status}
        )
    if transition.admin_only and not is_admin:
        raise drf_exceptions.PermissionDenied(_('Only the ADMIN role may perform this action.'))
    if transition.condition and not transition.condition(order):
        raise InvalidStatusTransitionError(transition.condition_message or _('The transition condition was not met.'))

    resulting_status = target_status
    # Cash on delivery never sits in PAID - it goes straight to PROCESSING.
    if target_status == OrderStatus.PAID and order.payment_method == PaymentMethod.CASH_ON_DELIVERY:
        resulting_status = OrderStatus.PROCESSING

    order.status = resulting_status

    if target_status == OrderStatus.PAID:
        order.payment_status = PaymentStatus.PAID
    if target_status == OrderStatus.SHIPPED:
        if _shipment_status_rank(order.shipment_status) < _shipment_status_rank(ShipmentStatus.HANDED_OVER):
            order.shipment_status = ShipmentStatus.HANDED_OVER
    if target_status == OrderStatus.DELIVERED:
        order.shipment_status = ShipmentStatus.DELIVERED
    if target_status == OrderStatus.REFUNDED:
        order.payment_status = PaymentStatus.REFUNDED

    order.save()
    return order
