"""Every order-lifecycle notification from chapter 6 of the API guide,
rendered through templates/emails/ via core.emails.send_templated_email.
"""
from django.conf import settings

from core.emails import send_templated_email
from core.exceptions import BusinessError

from .models import OrderStatus, PaymentMethod


def _order_access_url(order):
    from core.tokens import sign_order_access_token
    return f'{settings.FRONTEND_URL}/orders/{sign_order_access_token(order.id)}'


def send_order_received_email(order):
    send_templated_email(
        'order_received',
        {
            'order_number': order.order_number,
            'requires_quote': order.status == OrderStatus.QUOTE_REQUESTED,
            'url': _order_access_url(order),
        },
        subject=f'Objednávka {order.order_number} přijata',
        recipient_list=[order.customer.email],
    )


def send_shop_notification_email(order):
    notify_email = settings.SHOP_CONFIG['NOTIFY_EMAIL']
    if not notify_email:
        return
    send_templated_email(
        'shop_notification',
        {
            'order_number': order.order_number,
            'customer_email': order.customer.email,
            'has_custom': order.items.filter(is_custom=True).exists(),
            'admin_url': f'{settings.FRONTEND_URL}/admin/orders/{order.id}',
        },
        subject=f'Nová objednávka {order.order_number}',
        recipient_list=[notify_email],
    )


def send_order_confirmed_email(order, message=None):
    variable_symbol = ''.join(ch for ch in order.order_number if ch.isdigit())
    send_templated_email(
        'order_confirmed',
        {
            'order_number': order.order_number,
            'total': order.total,
            'shipping_cost': order.shipping_cost,
            'currency': order.currency,
            'bank_account': settings.SHOP_CONFIG['BANK_ACCOUNT'] if order.payment_method == PaymentMethod.BANK_TRANSFER else '',
            'variable_symbol': variable_symbol,
            'message': message,
            'url': _order_access_url(order),
        },
        subject=f'Objednávka {order.order_number} - potvrzení a platební údaje',
        recipient_list=[order.customer.email],
    )


def send_order_shipped_email(order):
    send_templated_email(
        'order_shipped',
        {
            'order_number': order.order_number,
            'carrier_name': order.carrier.name if order.carrier_id else '',
            'tracking_number': order.tracking_number,
            'url': _order_access_url(order),
        },
        subject=f'Objednávka {order.order_number} byla odeslána',
        recipient_list=[order.customer.email],
    )


def send_order_cancelled_email(order):
    send_templated_email(
        'order_cancelled',
        {'order_number': order.order_number},
        subject=f'Objednávka {order.order_number} byla zrušena',
        recipient_list=[order.customer.email],
    )


_RESEND_SENDERS = {
    'ORDER_RECEIVED': send_order_received_email,
    'ORDER_CONFIRMED': send_order_confirmed_email,
    'ORDER_SHIPPED': send_order_shipped_email,
    'ORDER_CANCELLED': send_order_cancelled_email,
}


def resend_order_email(order, email_type):
    sender = _RESEND_SENDERS.get(email_type)
    if sender is None:
        raise BusinessError('Neznámý typ e-mailu.', code='UNKNOWN_EMAIL_TYPE')
    sender(order)
