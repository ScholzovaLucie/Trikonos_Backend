import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_templated_email(template_name, context, subject, recipient_list):
    """Renders templates/emails/{template_name}.html and .txt and sends
    both as a multipart message - every order/account notification goes
    through this one function.

    Swallows and logs send failures instead of raising: every caller here
    is invoked from a transaction.on_commit callback, and an exception
    raised from on_commit propagates into the view's response cycle - an
    SMTP outage would otherwise turn into a 500 for an order that was
    already committed, inviting the customer to resubmit and double-order.
    """
    full_context = {
        **context,
        'shop_name': settings.SHOP_CONFIG['NAME'],
        'contact_email': settings.SHOP_CONFIG['CONTACT_EMAIL'],
    }
    text_body = render_to_string(f'emails/{template_name}.txt', full_context)
    html_body = render_to_string(f'emails/{template_name}.html', full_context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, recipient_list)
    message.attach_alternative(html_body, 'text/html')
    try:
        message.send()
    except Exception:
        logger.exception('Failed to send "%s" email to %s', template_name, recipient_list)
