"""
Trikonoš.cz — rozhraní k platební bráně.

Business logika (`services.py`) s bránou mluví jen přes tohle rozhraní, takže
nezáleží na tom, jakou bránu nakonec použijete (GoPay, Comgate, Stripe, ...).
Pro konkrétní bránu stačí napsat jednu třídu se dvěma metodami a nastavit ji:

    # settings.py
    TRIKONOS_PAYMENT_GATEWAY = "shop.payment_gateway.GoPayGateway"

Bezpečnostní zásada: stav platby se NIKDY nebere z těla notifikace (webhooku),
které může kdokoli podvrhnout. Notifikace jen oznámí "u platby X se něco změnilo"
a `services.handle_payment_notification` si skutečný stav vyžádá od brány přes
`get_status()` (odpovídá to doporučení všech běžných bran).

`DummyGateway` je pro vývoj a testy — žádná skutečná platba.
"""

import itertools
from dataclasses import dataclass, field

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from .models import PaymentStatus


@dataclass
class CreatedPayment:
    provider_payment_id: str   # ID platby u brány
    redirect_url: str          # adresa platební stránky, kam se zákazník přesměruje


@dataclass
class GatewayStatus:
    status: str                # hodnota z PaymentStatus (PENDING / PAID / FAILED / REFUNDED)
    raw: dict = field(default_factory=dict)   # celá odpověď brány, ukládá se k platbě pro dohledání


class PaymentGateway:
    """Rozhraní, které musí implementovat adaptér konkrétní brány."""

    code = ""   # krátký kód brány, ukládá se do Payment.provider (např. "gopay")

    def create_payment(
        self, *, order_number: str, amount: int, currency: str, return_url: str, customer_email: str,
    ) -> CreatedPayment:
        """Založí platbu u brány. `amount` je v haléřích. Vrací ID platby a adresu platební stránky."""
        raise NotImplementedError

    def get_status(self, provider_payment_id: str) -> GatewayStatus:
        """Zeptá se brány na AKTUÁLNÍ stav platby (zdroj pravdy — ne tělo notifikace)."""
        raise NotImplementedError


class DummyGateway(PaymentGateway):
    """
    Falešná brána pro vývoj a testy. Nic nestrhává; stav platby se nastavuje
    ručně: `DummyGateway.statuses[provider_payment_id] = PaymentStatus.PAID`.
    """

    code = "dummy"
    statuses: dict = {}
    _counter = itertools.count(1)

    @classmethod
    def reset(cls):
        cls.statuses = {}
        cls._counter = itertools.count(1)

    def create_payment(self, *, order_number, amount, currency, return_url, customer_email):
        payment_id = f"DUMMY-{next(self._counter)}"
        self.statuses[payment_id] = PaymentStatus.PENDING
        return CreatedPayment(payment_id, f"https://pay.example/{payment_id}")

    def get_status(self, provider_payment_id):
        status = self.statuses.get(provider_payment_id, PaymentStatus.PENDING)
        return GatewayStatus(status, {"dummy": True, "id": provider_payment_id, "status": status})


def get_gateway() -> PaymentGateway:
    """Vrátí nastavenou bránu. Bez nastavení je to chyba konfigurace (online platba nemá čím běžet)."""
    path = getattr(settings, "TRIKONOS_PAYMENT_GATEWAY", None)
    if not path:
        raise ImproperlyConfigured(
            "Není nastavená platební brána — nastavte TRIKONOS_PAYMENT_GATEWAY v settings.py."
        )
    return import_string(path)()
