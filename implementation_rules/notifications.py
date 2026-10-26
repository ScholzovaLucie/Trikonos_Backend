"""
Trikonoš.cz — e-maily k objednávkám.

Volá je services.py přes transaction.on_commit(), takže se e-mail odešle až
poté, co je objednávka opravdu uložená (nikdy se nestane, že zákazník dostane
potvrzení k objednávce, která se kvůli chybě nakonec nezapsala).

Nastavení (settings.py):
    DEFAULT_FROM_EMAIL = "Trikonoš <objednavky@trikonos.cz>"
    TRIKONOS_ADMIN_EMAIL = "info@trikonos.cz"   # kam chodí upozornění obchodu; None = neposílat
    TRIKONOS_ORDER_URL_TEMPLATE = "https://trikonos.cz/objednavka/{token}"
        # adresa stránky objednávky (tam zákazník zaplatí online); {token} = přístupový token

Texty jsou záměrně jednoduché — slouží jako výchozí bod, ne finální copy.
"""

from django.conf import settings
from django.core.mail import send_mail

from . import order_access
from .models import OrderStatus, PaymentMethod


def _money(halere: int) -> str:
    """Haléře → "1 098,00 Kč"."""
    kc = halere / 100
    return f"{kc:,.2f}".replace(",", " ").replace(".", ",") + " Kč"


def _send(subject: str, body: str, to: str):
    send_mail(subject, body, getattr(settings, "DEFAULT_FROM_EMAIL", None), [to])


def _order_page_url(order) -> str:
    """Odkaz na stránku objednávky (s přístupovým tokenem); prázdný, pokud není nastavená šablona adresy."""
    template = getattr(settings, "TRIKONOS_ORDER_URL_TEMPLATE", "")
    if not template:
        return ""
    return template.format(token=order_access.make_access_token(order.id))


def _payment_instructions(order) -> str:
    if order.payment_method == PaymentMethod.ONLINE:
        link = _order_page_url(order)
        if link:
            return f"\nZaplatit můžete online kartou nebo přes platební bránu zde:\n{link}\n"
        return "\nZaplatit můžete online na stránce své objednávky.\n"
    if order.payment_method == PaymentMethod.CASH_ON_DELIVERY:
        return "\nPlatba na dobírku — zaplatíte dopravci při převzetí.\n"
    return ""


def _price_summary(order) -> str:
    return f"Celkem: {_money(order.total)} (z toho doprava {_money(order.shipping_cost)})."


def order_created(order):
    """
    Po vytvoření objednávky: zákazníkovi potvrzení přijetí, obchodu upozornění.

    Objednávka, která je už potvrzená (katalogová z veřejného checkoutu), rovnou
    nese cenu a platební instrukce. Poptávka a nepotvrzená objednávka jen
    oznámí, že ji zpracováváme a cenu potvrdíme.
    """
    customer_email = order.customer.email

    if order.confirmed_at is not None:
        _send(
            f"Objednávka {order.order_number} přijata",
            f"Děkujeme za objednávku.\n\n{_price_summary(order)}\n"
            f"{_payment_instructions(order)}\nČíslo objednávky: {order.order_number}",
            customer_email,
        )
    elif order.status == OrderStatus.QUOTE_REQUESTED:
        _send(
            f"Poptávka {order.order_number} přijata",
            "Děkujeme, vaši poptávku jsme přijali. Návrh zkontrolujeme, ozveme se vám "
            "s cenou a až se na všem domluvíme, objednávku potvrdíme.\n\n"
            f"Číslo poptávky: {order.order_number}",
            customer_email,
        )
    else:
        _send(
            f"Objednávka {order.order_number} přijata",
            "Děkujeme, vaši objednávku jsme přijali a brzy vám ji potvrdíme.\n\n"
            f"Číslo objednávky: {order.order_number}",
            customer_email,
        )

    admin_email = getattr(settings, "TRIKONOS_ADMIN_EMAIL", None)
    if admin_email:
        awaiting = "Čeká na ocenění v administraci." if order.status == OrderStatus.QUOTE_REQUESTED else ""
        _send(
            f"Nová objednávka {order.order_number}",
            f"Zákazník: {customer_email}\nPočet položek: {order.items.count()}\n"
            f"Stav: {order.status}\n{awaiting}",
            admin_email,
        )


def order_confirmed(order, message: str = ""):
    """
    Potvrzení zákazníkovi (confirm_order): finální cena a platební instrukce,
    případně s osobním textem admina. Volá se i při opakovaném potvrzení
    (idempotentní — jen se e-mail pošle znovu, třeba po změně ceny).
    """
    personal = f"{message.strip()}\n\n" if message.strip() else ""
    _send(
        f"Objednávka {order.order_number} potvrzena",
        f"{personal}Vaše objednávka je potvrzena.\n\n{_price_summary(order)}\n"
        f"{_payment_instructions(order)}\nČíslo objednávky: {order.order_number}",
        order.customer.email,
    )


def status_changed(order, old_status):
    """Po změně stavu: e-mail jen u změn, které zákazníka zajímají."""
    customer_email = order.customer.email

    if order.status == OrderStatus.SHIPPED:
        tracking = f"\nSledovací číslo: {order.tracking_number}" if order.tracking_number else ""
        _send(
            f"Objednávka {order.order_number} odeslána",
            f"Vaše objednávka je na cestě.{tracking}",
            customer_email,
        )
    elif order.status == OrderStatus.CANCELLED:
        _send(
            f"Objednávka {order.order_number} zrušena",
            "Vaše objednávka byla zrušena. V případě dotazů nás kontaktujte.",
            customer_email,
        )
    elif order.status == OrderStatus.REFUNDED:
        _send(
            f"Objednávka {order.order_number} — vrácení platby",
            "Platbu za vaši objednávku vracíme.",
            customer_email,
        )


def payment_received(order):
    """Zákazníkovi: platba přes bránu přišla, jde se vyrábět."""
    _send(
        f"Platba za objednávku {order.order_number} přijata",
        f"Děkujeme, vaši platbu ({_money(order.total)}) jsme přijali a začínáme s výrobou.\n\n"
        f"Číslo objednávky: {order.order_number}",
        order.customer.email,
    )


def payment_needs_attention(order, payment, reason: str):
    """Obchodu: platba přišla, ale automaticky se nedá zařadit (typicky je potřeba ji vrátit)."""
    admin_email = getattr(settings, "TRIKONOS_ADMIN_EMAIL", None)
    if not admin_email:
        return
    _send(
        f"Platba k objednávce {order.order_number} vyžaduje pozornost",
        f"{reason}\n\nObjednávka: {order.order_number} (stav {order.status})\n"
        f"Zákazník: {order.customer.email}\n"
        f"Platba: {payment.provider} / {payment.provider_payment_id}, {_money(payment.amount)}",
        admin_email,
    )
