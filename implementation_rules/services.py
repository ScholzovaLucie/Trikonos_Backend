"""
Trikonoš.cz — business logika objednávání (Django service vrstva).

Všechna pravidla objednávání žijí TADY, ne v API views, admin akcích ani
formulářích — ty jen volají tyhle funkce. Díky tomu se pravidla nedají obejít
(např. změnou stavu přímo v Django adminu přes .save()), pokud se změny jdou
přes funkce z tohoto modulu.

Hlavní principy
---------------
1. Vlastní potisk (a každá neoceněná položka) ocení admin; objednávka jen z
   hotových motivů má cenu hned.
2. Cena dopravy je VŽDY známá a závisí jen na CELKOVÉM POČTU KUSŮ (jedna
   základní cena + slevové prahy). Proto je známá i u poptávky, která ještě
   nemá cenu zboží.
3. Zákazníkovi se potvrzuje cena. Objednávka může do výroby až po potvrzení
   (`confirmed_at`) a jakákoli změna ceny potvrzení zruší.
4. Stav objednávky, stav platby i stav zásilky se mění jen přes funkce tady,
   které hlídají povolené přechody a role.
5. Platí se ONLINE přes platební bránu, nebo na dobírku. Stav online platby
   určuje brána — ne klient a ne tělo notifikace (viz payment_gateway.py).

Ceny jsou všude v haléřích (int), stejně jako v databázi.
Viz Trikonos_business_logika.md pro popis pravidel a otevřené otázky.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone

from . import design_files, notifications, payment_gateway
from .models import (
    SIZE_CHOICES,
    Carrier,
    Color,
    Customer,
    Cut,
    CustomDesignPlacement,
    DeliveryType,
    Motif,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
    PrintArea,
    ProductType,
    ShipmentStatus,
    ShippingQuantityDiscount,
    ShippingSettings,
    StaffRole,
)

logger = logging.getLogger(__name__)

VALID_SIZES = {value for value, _ in SIZE_CHOICES}
MAX_ITEM_QUANTITY = 500


class InvalidTransition(ValidationError):
    """Změna stavu, která není povolená (nebo nesplňuje podmínky) — API: 409 INVALID_STATUS_TRANSITION."""


class OrderLocked(ValidationError):
    """Objednávku už nejde upravovat (není ve stavu QUOTE_REQUESTED/NEW) — API: 409 ORDER_LOCKED."""


class ShippingPriceNotConfigured(ImproperlyConfigured):
    """
    Není nastavená základní cena dopravy. Je to chyba konfigurace, ne chyba
    zákazníka: cena dopravy musí být vždy známá, takže se objednávky odmítají,
    dokud ji admin nenastaví. API by ji mělo vrátit jako 503 a zalogovat.
    """


# Rozlišení "pole nepřišlo" od "pole přišlo jako null" u částečných úprav.
UNSET = object()


# -----------------------------------------------------------------------------
# VSTUPNÍ DATA (to, co posílá frontend/API)
# -----------------------------------------------------------------------------

@dataclass
class ContactInput:
    email: str
    phone: str = ""
    first_name: str = ""
    last_name: str = ""
    company_name: str = ""
    marketing_consent: bool = False


@dataclass
class AddressInput:
    full_name: str
    street: str
    city: str
    zip: str
    country: str = "CZ"
    company_name: str = ""
    ico: str = ""   # jen fakturační adresa
    dic: str = ""   # jen fakturační adresa


@dataclass
class DeliveryInput:
    delivery_type: str                      # DeliveryType.PICKUP_POINT / HOME_DELIVERY
    carrier_id: Optional[uuid.UUID] = None
    pickup_point_id: str = ""
    pickup_point_name: str = ""


@dataclass
class PlacementInput:
    """
    Jedno umístění vlastního návrhu v bloku (v mm, jako print_areas.width_mm/height_mm).
    Soubor se předává TOKENEM z uploadu, nikdy adresou — viz design_files.py.
    """
    print_area_id: uuid.UUID
    file_token: str
    x: int
    y: int
    width: int
    height: int
    rotation_deg: int = 0


@dataclass
class ItemInput:
    """
    Jedna položka košíku. Katalogová = zadané motif_id (a žádná placements).
    Vlastní potisk = bez motif_id, s aspoň jedním placement.

    `unit_price` se bere v úvahu JEN u objednávky zakládané adminem
    (by_admin=True) — u veřejného checkoutu se ignoruje.
    """
    product_type_id: uuid.UUID
    cut_id: uuid.UUID
    color_id: uuid.UUID
    size: str
    quantity: int = 1
    motif_id: Optional[uuid.UUID] = None
    placements: list[PlacementInput] = field(default_factory=list)
    custom_text: str = ""
    unit_price: Optional[int] = None


# -----------------------------------------------------------------------------
# POMOCNÉ FUNKCE
# -----------------------------------------------------------------------------

def _active(model, pk, label):
    """Načte aktivní řádek číselníku, jinak ValidationError s čitelnou hláškou."""
    obj = model.objects.filter(pk=pk, is_active=True).first()
    if obj is None:
        raise ValidationError(f"{label} neexistuje nebo už není dostupný.")
    return obj


def _check_money(value, label):
    if value < 0:
        raise ValidationError(f"{label} nesmí být záporná.")


def generate_order_number() -> str:
    """
    Číslo objednávky z DB sekvence order_number_seq (bezkolizní i při souběžných
    objednávkách). Na jiné DB než PostgreSQL (typicky testy na SQLite) sekvence
    není, použije se jednoduchý náhradní čítač.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT nextval('order_number_seq')")
            seq = cursor.fetchone()[0]
    else:
        seq = Order.objects.count() + 1
    return f"TRK-{date.today().year}-{seq:05d}"


def upsert_customer(contact: ContactInput) -> Customer:
    """
    Najde zákazníka podle e-mailu (bez ohledu na velikost písmen), nebo ho založí.
    U existujícího doplní jen to, co zadal nově — nic nemaže. Souhlas
    s marketingem se může jen zapnout, nikdy se sám nevypne tím, že
    zákazník u další objednávky políčko neoznačil.
    """
    email = contact.email.strip().lower()
    if not email:
        raise ValidationError("E-mail je povinný.")
    customer, _ = Customer.objects.get_or_create(email=email)

    changed = False
    for attr in ("phone", "first_name", "last_name", "company_name"):
        new_value = getattr(contact, attr).strip()
        if new_value and getattr(customer, attr) != new_value:
            setattr(customer, attr, new_value)
            changed = True
    if contact.marketing_consent and not customer.marketing_consent:
        customer.marketing_consent = True
        changed = True
    if changed:
        customer.save()
    return customer


# -----------------------------------------------------------------------------
# CENA DOPRAVY
# -----------------------------------------------------------------------------

def get_base_shipping_price() -> int:
    """Základní cena dopravy (haléře). Není-li nastavená, je to chyba konfigurace."""
    price = ShippingSettings.load().price
    if price is None:
        raise ShippingPriceNotConfigured(
            "Není nastavená cena dopravy — nastavte ji v administraci (Doprava → cena)."
        )
    return price


def calculate_shipping_cost(total_quantity: int) -> int:
    """
    Cena dopravy pro objednávku s daným CELKOVÝM počtem kusů: základní cena
    snížená o slevový práh s nejvyšším `min_quantity`, který je splněný
    (100 % = zdarma). Bez shody platí plná cena. Zaokrouhluje se na haléře
    matematicky (půl haléře nahoru).
    """
    base = get_base_shipping_price()
    discount = (
        ShippingQuantityDiscount.objects
        .filter(is_active=True, min_quantity__lte=total_quantity)
        .order_by("-min_quantity")
        .first()
    )
    percent = discount.discount_percent if discount else 0
    return (base * (100 - percent) + 50) // 100


def _apply_totals(order: Order, items=None):
    """Přepočítá subtotal a total z položek a uložené ceny dopravy (nic nezapisuje do DB)."""
    items = list(order.items.all()) if items is None else items
    if any(item.unit_price is None for item in items):
        order.subtotal = None
        order.total = None
        return
    order.subtotal = sum(item.unit_price * item.quantity for item in items)
    order.total = None if order.shipping_cost is None else order.subtotal + order.shipping_cost


# -----------------------------------------------------------------------------
# SESTAVENÍ A VALIDACE POLOŽEK
# -----------------------------------------------------------------------------

def _validate_placement(placement: PlacementInput, product_type: ProductType) -> PrintArea:
    area = _active(PrintArea, placement.print_area_id, "Blok pro potisk")
    if area.product_type_id != product_type.id:
        raise ValidationError(
            f'Blok "{area.name}" nepatří k typu oblečení "{product_type.name}".'
        )
    if placement.width <= 0 or placement.height <= 0:
        raise ValidationError("Rozměr umístění musí být větší než nula.")
    if placement.x < 0 or placement.y < 0:
        raise ValidationError("Umístění nesmí začínat mimo blok.")
    if placement.x + placement.width > area.width_mm or placement.y + placement.height > area.height_mm:
        raise ValidationError(
            f'Umístění přesahuje blok "{area.name}" ({area.width_mm}×{area.height_mm} mm).'
        )
    return area


def _build_item(inp: ItemInput, *, by_admin: bool = False):
    """Zvaliduje jednu položku a vrátí (nezapsaný OrderItem, [nezapsaná umístění])."""
    if not 1 <= inp.quantity <= MAX_ITEM_QUANTITY:
        raise ValidationError(f"Množství musí být od 1 do {MAX_ITEM_QUANTITY}.")
    if inp.size not in VALID_SIZES:
        raise ValidationError(f"Neplatná velikost: {inp.size}.")

    product_type = _active(ProductType, inp.product_type_id, "Typ oblečení")
    cut = _active(Cut, inp.cut_id, "Střih")
    color = _active(Color, inp.color_id, "Barva")
    spec = f"{product_type.name} / {cut.name} / {color.name} / {inp.size}"

    # Cenu od klienta bereme jen od admina; zákazníkova hodnota se ignoruje.
    admin_price = inp.unit_price if by_admin else None
    if admin_price is not None:
        _check_money(admin_price, "Cena")

    if inp.motif_id is not None:
        # Katalogová položka: jeden hotový motiv, cena hned známá.
        if inp.placements:
            raise ValidationError("Katalogová položka nesmí obsahovat vlastní umístění.")
        motif = _active(Motif, inp.motif_id, "Motiv")
        item = OrderItem(
            is_custom=False, motif=motif, custom_text="",
            product_type=product_type, cut=cut, color=color, size=inp.size,
            item_label=f"{motif.name} / {spec}",
            unit_price=admin_price if admin_price is not None else motif.price,  # snapshot
            quantity=inp.quantity,
        )
        return item, []

    # Vlastní potisk: aspoň jedno umístění, cenu nastaví admin.
    if not inp.placements:
        raise ValidationError("Vlastní potisk musí mít aspoň jedno umístění návrhu.")
    placements = []
    for p in inp.placements:
        area = _validate_placement(p, product_type)
        url = design_files.resolve_file_token(p.file_token)   # neplatný token → INVALID_FILE_TOKEN
        placements.append(CustomDesignPlacement(
            print_area=area, url=url, x=p.x, y=p.y,
            width=p.width, height=p.height, rotation_deg=p.rotation_deg,
        ))
    item = OrderItem(
        is_custom=True, motif=None, custom_text=inp.custom_text.strip(),
        product_type=product_type, cut=cut, color=color, size=inp.size,
        item_label=f"Vlastní potisk / {spec}",
        unit_price=admin_price,       # veřejně vždy None — cenu nastavuje admin
        quantity=inp.quantity,
    )
    return item, placements


def _validate_delivery(delivery: DeliveryInput):
    if delivery.delivery_type not in DeliveryType.values:
        raise ValidationError("Neplatný způsob doručení.")
    if delivery.delivery_type == DeliveryType.PICKUP_POINT and not (
        delivery.pickup_point_id and delivery.pickup_point_name
    ):
        raise ValidationError("Vyberte výdejní místo.")
    if delivery.carrier_id is None:
        raise ValidationError("Vyberte dopravce.")
    _active(Carrier, delivery.carrier_id, "Dopravce")


# -----------------------------------------------------------------------------
# OVĚŘENÍ KOŠÍKU (krok "Shrnutí" ve frontendu)
# -----------------------------------------------------------------------------

@dataclass
class CartLineResult:
    valid: bool
    errors: list[str]
    item_label: Optional[str]
    unit_price: Optional[int]      # None u vlastního potisku nebo chybné položky
    line_total: Optional[int]      # unit_price × quantity, None když cena chybí


@dataclass
class CartValidation:
    items: list[CartLineResult]
    subtotal: Optional[int]        # None, pokud je položka neplatná NEBO bez ceny (vlastní potisk)
    requires_quote: bool           # v košíku je vlastní potisk → cenu potvrdíme e-mailem
    shipping_cost: Optional[int]   # None jen když je některá položka neplatná
    total: Optional[int]


def validate_cart(items: list[ItemInput]) -> CartValidation:
    """
    Spočítá, co by se stalo, kdyby se objednávka s těmito položkami odeslala
    teď. NIC neukládá. Frontend volá při otevření košíku a těsně před odesláním.

    Cena dopravy je u každého platného košíku známá (i s vlastním potiskem,
    protože závisí na počtu kusů, ne na ceně). Cena se počítá na serveru z
    ověřených položek, z klienta se nepřebírá nic.
    """
    if not items:
        raise ValidationError("Košík je prázdný.")

    lines, built = [], []
    for inp in items:
        try:
            item, _ = _build_item(inp, by_admin=False)
        except ValidationError as exc:
            lines.append(CartLineResult(False, list(exc.messages), None, None, None))
            built.append(None)
            continue
        line_total = None if item.unit_price is None else item.unit_price * item.quantity
        lines.append(CartLineResult(True, [], item.item_label, item.unit_price, line_total))
        built.append(item)

    all_valid = all(item is not None for item in built)
    requires_quote = any(inp.motif_id is None for inp in items)
    subtotal = (
        sum(line.line_total for line in lines)
        if all_valid and all(line.line_total is not None for line in lines) else None
    )
    shipping_cost = calculate_shipping_cost(sum(item.quantity for item in built)) if all_valid else None
    total = subtotal + shipping_cost if subtotal is not None and shipping_cost is not None else None
    return CartValidation(lines, subtotal, requires_quote, shipping_cost, total)


# -----------------------------------------------------------------------------
# VYTVOŘENÍ OBJEDNÁVKY
# -----------------------------------------------------------------------------

@transaction.atomic
def create_order(
    *,
    contact: ContactInput,
    billing: AddressInput,
    shipping: AddressInput,
    delivery: DeliveryInput,
    items: list[ItemInput],
    payment_method: str,
    note: str = "",
    terms_accepted: bool = False,
    by_admin: bool = False,
    shipping_cost: Optional[int] = None,
    order_id: Optional[uuid.UUID] = None,
) -> Order:
    """
    Založí objednávku z košíku. Vrací Order.

    - Stav: QUOTE_REQUESTED, pokud je některá položka bez ceny (vlastní potisk);
      jinak NEW s hotovou cenou včetně dopravy.
    - Doprava se spočítá z celkového počtu kusů hned při vzniku (i u poptávky).
    - Potvrzení: katalogová objednávka z veřejného checkoutu se potvrdí sama
      (zákazník dostane hned platební instrukce) — vypíná se nastavením
      TRIKONOS_AUTO_CONFIRM_CATALOG_ORDERS = False. Poptávku a objednávky
      zakládané adminem potvrzuje admin ručně (confirm_order).
    - by_admin=True (ruční objednávka přijatá telefonem/e-mailem): nevyžaduje
      souhlas s podmínkami a smí obsahovat ceny položek (ItemInput.unit_price)
      a ruční cenu dopravy (`shipping_cost`). U veřejného checkoutu se obojí
      IGNORUJE — zákazník si cenu nastavit nemůže.
    - `order_id` (volitelné): UUID vygenerované klientem při otevření checkoutu.
      Existuje-li objednávka s tímhle ID (dvojklik, opakování po výpadku
      sítě), vrátí se původní místo duplicity.
    - Košík žije na klientovi; server tu znovu zvaliduje a přepočítá VŠE.
    """
    if order_id is not None:
        existing = Order.objects.filter(pk=order_id).first()
        if existing is not None:
            return existing

    if not by_admin and not terms_accepted:
        raise ValidationError(
            "Pro odeslání objednávky je nutné souhlasit s obchodními podmínkami.",
            code="TERMS_NOT_ACCEPTED",
        )
    if not items:
        raise ValidationError("Objednávka musí obsahovat aspoň jednu položku.")
    if payment_method not in PaymentMethod.values:
        raise ValidationError("Zvolte způsob platby.")
    _validate_delivery(delivery)

    manual_shipping = by_admin and shipping_cost is not None
    if manual_shipping:
        _check_money(shipping_cost, "Cena dopravy")

    customer = upsert_customer(contact)
    built = [_build_item(item, by_admin=by_admin) for item in items]
    order_items = [item for item, _ in built]

    order = Order(
        order_number=generate_order_number(),
        customer=customer,
        shipping_cost=shipping_cost if manual_shipping else calculate_shipping_cost(
            sum(item.quantity for item in order_items)
        ),
        shipping_cost_manual=manual_shipping,
        carrier_id=delivery.carrier_id,
        delivery_type=delivery.delivery_type,
        pickup_point_id=delivery.pickup_point_id,
        pickup_point_name=delivery.pickup_point_name,
        payment_method=payment_method,
        billing_full_name=billing.full_name, billing_company_name=billing.company_name,
        billing_ico=billing.ico, billing_dic=billing.dic,
        billing_street=billing.street, billing_city=billing.city,
        billing_zip=billing.zip, billing_country=billing.country,
        shipping_full_name=shipping.full_name, shipping_company_name=shipping.company_name,
        shipping_street=shipping.street, shipping_city=shipping.city,
        shipping_zip=shipping.zip, shipping_country=shipping.country,
        note=note.strip(),
    )
    _apply_totals(order, order_items)
    order.status = OrderStatus.QUOTE_REQUESTED if order.total is None else OrderStatus.NEW
    if (
        order.status == OrderStatus.NEW and not by_admin
        and getattr(settings, "TRIKONOS_AUTO_CONFIRM_CATALOG_ORDERS", True)
    ):
        order.confirmed_at = timezone.now()
    if order_id is not None:
        order.id = order_id
    order.save()

    for item, placements in built:
        item.order = order
        item.save()
        for placement in placements:
            placement.order_item = item
            placement.save()

    transaction.on_commit(lambda: notifications.order_created(order))
    return order


# -----------------------------------------------------------------------------
# ÚPRAVY CENY A POTVRZENÍ (QUOTE_REQUESTED / NEW)
# -----------------------------------------------------------------------------

EDITABLE_STATUSES = (OrderStatus.QUOTE_REQUESTED, OrderStatus.NEW)


def _locked_order(order_id) -> Order:
    return Order.objects.select_for_update().get(pk=order_id)


def _cancel_confirmation(order: Order):
    """
    Cena se změnila: zruš potvrzení a rozpracované online platby ve staré výši,
    aby zákazník nemohl zaplatit částku, která už neplatí. Zaplatit pak může až
    po novém potvrzení (nový odkaz v e-mailu).
    """
    order.confirmed_at = None
    order.payments.filter(status=PaymentStatus.PENDING).update(status=PaymentStatus.FAILED)


def _ensure_editable(order: Order):
    """Cenu, položky, adresy a dopravu jde měnit jen do potvrzení výroby."""
    if order.status not in EDITABLE_STATUSES:
        raise OrderLocked(
            "Objednávku už nejde upravovat (je ve výrobě, odeslaná nebo uzavřená).",
            code="ORDER_LOCKED",
        )


@transaction.atomic
def price_item(order_id, item_id, *, unit_price=UNSET, quantity=UNSET) -> Order:
    """
    Ocení položku a/nebo změní počet kusů (hlavní nástroj pro ocenění poptávky).
    Pošli jen to, co se mění. `unit_price=None` vrátí položku do stavu
    "čeká na ocenění".

    Po změně se přepočítá subtotal a total. Změní-li se počet kusů, přepočítá se
    i doprava (slevové prahy závisí na celkovém počtu kusů) — pokud ji ale admin
    nenastavil ručně (set_shipping_cost). Jakákoli skutečná změna zruší
    potvrzení (confirmed_at → None): zákazník dostal jinou cenu, než teď platí,
    a admin musí potvrdit znovu. Nic zákazníkovi samo nechodí.
    """
    order = _locked_order(order_id)
    _ensure_editable(order)
    item = order.items.filter(pk=item_id).first()
    if item is None:
        raise ValidationError("Položka v objednávce neexistuje.", code="ITEM_NOT_FOUND")

    changed = quantity_changed = False
    if unit_price is not UNSET:
        if unit_price is not None:
            _check_money(unit_price, "Cena")
        if item.unit_price != unit_price:
            item.unit_price, changed = unit_price, True
    if quantity is not UNSET:
        if not 1 <= quantity <= MAX_ITEM_QUANTITY:
            raise ValidationError(f"Množství musí být od 1 do {MAX_ITEM_QUANTITY}.")
        if item.quantity != quantity:
            item.quantity, changed, quantity_changed = quantity, True, True
    if not changed:
        return order

    item.save()
    items = list(order.items.all())
    if quantity_changed and not order.shipping_cost_manual:
        order.shipping_cost = calculate_shipping_cost(sum(i.quantity for i in items))
    _apply_totals(order, items)
    _cancel_confirmation(order)
    order.save()
    return order


@transaction.atomic
def set_shipping_cost(order_id, shipping_cost: int) -> Order:
    """
    Ruční cena dopravy (výjimka z automatického výpočtu, např. dohodnutá jiná
    cena). Od teď se při změně počtu kusů nepřepočítává — zpět na automatiku
    vrací reset_shipping_cost(). Změna ceny zruší potvrzení.
    """
    order = _locked_order(order_id)
    _ensure_editable(order)
    _check_money(shipping_cost, "Cena dopravy")

    value_changed = order.shipping_cost != shipping_cost
    order.shipping_cost = shipping_cost
    order.shipping_cost_manual = True
    _apply_totals(order)
    if value_changed:
        _cancel_confirmation(order)
    order.save()
    return order


@transaction.atomic
def reset_shipping_cost(order_id) -> Order:
    """Vrátí cenu dopravy na automatický výpočet podle počtu kusů."""
    order = _locked_order(order_id)
    _ensure_editable(order)

    new_cost = calculate_shipping_cost(sum(i.quantity for i in order.items.all()))
    value_changed = order.shipping_cost != new_cost
    order.shipping_cost = new_cost
    order.shipping_cost_manual = False
    _apply_totals(order)
    if value_changed:
        _cancel_confirmation(order)
    order.save()
    return order


@transaction.atomic
def confirm_order(order_id, *, message: str = "") -> Order:
    """
    Potvrdí objednávku zákazníkovi: pošle e-mail s finální cenou a platebními
    instrukcemi, nastaví `confirmed_at` (podmínka pro zahájení výroby) a
    poptávku (QUOTE_REQUESTED) posune na NEW. Vyžaduje, aby měla objednávka
    celkovou cenu.

    Je IDEMPOTENTNÍ: opakované volání jen znovu pošle e-mail (původní čas
    potvrzení zůstane), nic se tím nerozbije. `message` je osobní text admina
    vložený do e-mailu.
    """
    order = _locked_order(order_id)
    if order.status not in EDITABLE_STATUSES:
        raise InvalidTransition("Potvrdit jde jen objednávku, která ještě nejde do výroby.")
    if order.total is None:
        raise ValidationError(
            "Objednávka ještě nemá cenu — ocente všechny položky.", code="ORDER_NOT_PRICED",
        )

    if order.confirmed_at is None:
        order.confirmed_at = timezone.now()
    if order.status == OrderStatus.QUOTE_REQUESTED:
        order.status = OrderStatus.NEW
    order.save()
    transaction.on_commit(lambda: notifications.order_confirmed(order, message))
    return order


# -----------------------------------------------------------------------------
# ADMIN VYTVÁŘÍ OBJEDNÁVKU (telefon / e-mail)
# -----------------------------------------------------------------------------

def create_order_by_admin(**kwargs) -> Order:
    """Ruční objednávka: stejná pravidla jako veřejná, ale bez podmínek a se zadanými cenami (viz create_order)."""
    return create_order(by_admin=True, **kwargs)


# -----------------------------------------------------------------------------
# STAVOVÝ AUTOMAT OBJEDNÁVKY
# -----------------------------------------------------------------------------

# Přechod QUOTE_REQUESTED → NEW tu NENÍ záměrně: dělá ho jen confirm_order(),
# aby se vždycky nastavilo potvrzení a odešel e-mail s cenou.
ALLOWED_TRANSITIONS = {
    OrderStatus.QUOTE_REQUESTED: {OrderStatus.CANCELLED},
    OrderStatus.NEW: {OrderStatus.PAID, OrderStatus.PROCESSING, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.PROCESSING, OrderStatus.REFUNDED},
    OrderStatus.PROCESSING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED, OrderStatus.REFUNDED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.REFUNDED},
    OrderStatus.DELIVERED: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}


def _is_admin(actor) -> bool:
    return getattr(actor, "role", None) == StaffRole.ADMIN


def _check_permissions(order: Order, new_status: str, actor):
    """
    Jen role ADMIN smí: vrátit platbu, zrušit objednávku už ve výrobě a ručně
    označit ONLINE objednávku jako zaplacenou (to normálně dělá platební brána
    — viz handle_payment_notification — takže ruční zásah je výjimka).
    """
    if new_status == OrderStatus.PAID and order.payment_method == PaymentMethod.ONLINE and not _is_admin(actor):
        raise PermissionDenied(
            "Online platbu potvrzuje platební brána; ručně ji smí označit jen administrátor."
        )
    admin_only = new_status == OrderStatus.REFUNDED or (
        order.status == OrderStatus.PROCESSING and new_status == OrderStatus.CANCELLED
    )
    if admin_only and not _is_admin(actor):
        raise PermissionDenied("Tuto změnu stavu smí provést jen administrátor.")


def _check_guards(order: Order, new_status: str):
    """Podmínky nad rámec toho, že přechod mezi stavy vůbec existuje."""
    if new_status == OrderStatus.PAID and order.total is None:
        raise InvalidTransition("Objednávku bez ceny nejde označit jako zaplacenou.")

    if new_status == OrderStatus.PROCESSING:
        if order.confirmed_at is None:
            raise InvalidTransition("Objednávka není potvrzená zákazníkovi — nejdřív ji potvrďte.")
        if order.payment_method is None:
            raise InvalidTransition("Před zahájením výroby musí být zvolený způsob platby.")
        if order.payment_method == PaymentMethod.ONLINE and order.payment_status != PaymentStatus.PAID:
            raise InvalidTransition("Online platba musí být nejdřív zaplacená.")

    if new_status == OrderStatus.SHIPPED and order.carrier_id is None:
        raise InvalidTransition("Před odesláním musí být zvolený dopravce.")

    if new_status == OrderStatus.CANCELLED and order.payment_status == PaymentStatus.PAID:
        raise InvalidTransition("Zaplacenou objednávku jde jen vrátit (REFUNDED), ne zrušit.")

    if new_status == OrderStatus.REFUNDED and order.payment_status != PaymentStatus.PAID:
        raise InvalidTransition("Vrátit jde jen zaplacenou objednávku (nezaplacenou zrušte).")


def _apply_side_effects(order: Order, new_status: str):
    """Navazující změny, které se se stavem musí dít vždy společně."""
    if new_status == OrderStatus.PAID:
        order.payment_status = PaymentStatus.PAID
    elif new_status == OrderStatus.REFUNDED:
        order.payment_status = PaymentStatus.REFUNDED
        order.payments.filter(status=PaymentStatus.PAID).update(status=PaymentStatus.REFUNDED)
    elif new_status == OrderStatus.SHIPPED:
        if order.shipment_status in (ShipmentStatus.CREATED, ShipmentStatus.LABEL_PRINTED):
            order.shipment_status = ShipmentStatus.HANDED_OVER
    elif new_status == OrderStatus.DELIVERED:
        order.shipment_status = ShipmentStatus.DELIVERED
        if order.payment_method == PaymentMethod.CASH_ON_DELIVERY:
            order.payment_status = PaymentStatus.PAID   # dobírku vybral dopravce


def _transition_locked(order: Order, new_status: str) -> Order:
    """
    Jádro změny stavu nad už zamčenou objednávkou: povolené přechody, podmínky,
    navazující pole, uložení a e-mail. Role se tu NEkontrolují — to dělá
    transition_order; tohle volá i systém (potvrzení platby od brány), který
    žádného uživatele nemá.
    """
    old_status = order.status
    if new_status not in ALLOWED_TRANSITIONS.get(old_status, set()):
        hint = " Poptávku potvrďte přes potvrzení objednávky." if (
            old_status == OrderStatus.QUOTE_REQUESTED and new_status == OrderStatus.NEW
        ) else ""
        raise InvalidTransition(f"Přechod {old_status} → {new_status} není povolený.{hint}")
    _check_guards(order, new_status)
    _apply_side_effects(order, new_status)

    order.status = new_status
    order.save()
    transaction.on_commit(lambda: notifications.status_changed(order, old_status))
    return order


@transaction.atomic
def transition_order(order_id, new_status: str, *, actor) -> Order:
    """
    JEDINÉ místo, přes které uživatel administrace mění stav objednávky. Hlídá
    povolené přechody, podmínky a role, a dopočítá navazující pole (platba,
    doprava). Řádek se na dobu změny zamkne, takže dvě souběžné změny nemůžou
    rozbít stav.

    `actor` je přihlášený uživatel administrace (rozhoduje o rolích). Dobírka
    nikdy neprochází stavem PAID — požadavek na PAID se u ní přesměruje na
    PROCESSING (peníze vybere dopravce až při doručení). ONLINE objednávku jako
    zaplacenou normálně označí platební brána, ručně jen ADMIN.
    """
    order = _locked_order(order_id)
    if new_status == OrderStatus.PAID and order.payment_method == PaymentMethod.CASH_ON_DELIVERY:
        new_status = OrderStatus.PROCESSING
    _check_permissions(order, new_status, actor)
    return _transition_locked(order, new_status)


# Pohodlné zkratky pro admin akce / API
def mark_paid(order_id, *, actor) -> Order:
    return transition_order(order_id, OrderStatus.PAID, actor=actor)


def start_processing(order_id, *, actor) -> Order:
    return transition_order(order_id, OrderStatus.PROCESSING, actor=actor)


@transaction.atomic
def ship(order_id, *, actor, tracking_number: str = "") -> Order:
    if tracking_number:
        Order.objects.filter(pk=order_id).update(tracking_number=tracking_number.strip())
    return transition_order(order_id, OrderStatus.SHIPPED, actor=actor)


def mark_delivered(order_id, *, actor) -> Order:
    return transition_order(order_id, OrderStatus.DELIVERED, actor=actor)


def cancel(order_id, *, actor) -> Order:
    return transition_order(order_id, OrderStatus.CANCELLED, actor=actor)


def refund(order_id, *, actor) -> Order:
    return transition_order(order_id, OrderStatus.REFUNDED, actor=actor)


# -----------------------------------------------------------------------------
# ONLINE PLATBA PŘES PLATEBNÍ BRÁNU
# -----------------------------------------------------------------------------

@dataclass
class PaymentOutcome:
    """Výsledek zpracování notifikace od brány."""
    payment: Payment
    order_marked_paid: bool         # objednávka přešla do PAID
    needs_attention: bool = False   # peníze přišly, ale objednávku nejde označit jako zaplacenou → řeší člověk
    reason: str = ""


@transaction.atomic
def start_online_payment(order_id, *, return_url: str) -> Payment:
    """
    Založí u platební brány nový pokus o platbu a vrátí `Payment` s `redirect_url`
    (kam zákazníka přesměrovat). Lze volat opakovaně — nový pokus nahradí
    rozpracovaný (starý se označí jako neúspěšný), takže je vždy nejvýš jeden aktivní.

    Zaplatit jde jen **potvrzenou** online objednávku s cenou a ve stavu NEW.
    Poptávka musí nejdřív projít potvrzením (zákazník musí znát finální cenu),
    a po změně ceny se potvrzení ruší — platí se až po novém potvrzení.
    Částka platby se bere z objednávky na serveru, klient ji neposílá.
    """
    order = _locked_order(order_id)
    if order.payment_method != PaymentMethod.ONLINE:
        raise ValidationError("Tahle objednávka se neplatí online.")
    if order.status != OrderStatus.NEW:
        raise InvalidTransition("Zaplatit jde jen potvrzenou objednávku, která čeká na platbu.")
    if order.confirmed_at is None or order.total is None:
        raise ValidationError(
            "Objednávka ještě není potvrzená — platit jde až po potvrzení ceny.", code="ORDER_NOT_CONFIRMED",
        )
    if order.payment_status == PaymentStatus.PAID:
        raise InvalidTransition("Objednávka je už zaplacená.")

    gateway = payment_gateway.get_gateway()
    order.payments.filter(status=PaymentStatus.PENDING).update(status=PaymentStatus.FAILED)
    created = gateway.create_payment(
        order_number=order.order_number, amount=order.total, currency=order.currency,
        return_url=return_url, customer_email=order.customer.email,
    )
    payment = Payment.objects.create(
        order=order, provider=gateway.code, provider_payment_id=created.provider_payment_id,
        amount=order.total, redirect_url=created.redirect_url, status=PaymentStatus.PENDING,
    )
    order.payment_status = PaymentStatus.PENDING
    order.save()
    return payment


def _flag_attention(order: Order, payment: Payment, reason: str) -> PaymentOutcome:
    """Peníze přišly, ale automaticky se s nimi nedá nic rozumného — upozorni obchod."""
    logger.warning("Platba %s k objednávce %s vyžaduje pozornost: %s", payment, order.order_number, reason)
    transaction.on_commit(lambda: notifications.payment_needs_attention(order, payment, reason))
    return PaymentOutcome(payment, order_marked_paid=False, needs_attention=True, reason=reason)


def _apply_successful_payment(order: Order, payment: Payment) -> PaymentOutcome:
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        return _flag_attention(order, payment, "Platba přišla k zrušené nebo vrácené objednávce — je potřeba ji vrátit.")
    if order.status != OrderStatus.NEW:
        return _flag_attention(order, payment, "Objednávka už je zaplacená — duplicitní platba, je potřeba ji vrátit.")
    if payment.amount != order.total:
        return _flag_attention(
            order, payment, "Zaplacená částka neodpovídá aktuální ceně objednávky (cena se po založení platby změnila).",
        )
    _transition_locked(order, OrderStatus.PAID)     # nastaví i payment_status = PAID
    transaction.on_commit(lambda: notifications.payment_received(order))
    return PaymentOutcome(payment, order_marked_paid=True)


@transaction.atomic
def handle_payment_notification(provider: str, provider_payment_id: str) -> PaymentOutcome:
    """
    Zpracuje oznámení od platební brány, že se u platby něco změnilo (webhook
    nebo návrat zákazníka z platební stránky).

    - **Skutečný stav se vyžádá od brány** (`get_status`), ne z těla oznámení —
      to může kdokoli podvrhnout.
    - **Je idempotentní:** brány notifikace opakují, druhé zpracování téhož stavu
      nic nezmění a nic znovu neodešle.
    - Úspěšná platba posune objednávku do PAID. Pokud to nejde (objednávka mezitím
      zrušena, už zaplacená jinak, změnila se cena), platba se zaznamená, objednávka
      se NEMĚNÍ a vrátí se `needs_attention=True` + upozornění obchodu — peníze
      přišly, takže je musí vyřešit člověk (typicky vrátit).
    - Neúspěšná platba objednávku nechá ve stavu NEW; zákazník může zaplatit znovu.
    """
    payment = Payment.objects.select_for_update().filter(
        provider=provider, provider_payment_id=provider_payment_id,
    ).first()
    if payment is None:
        raise ValidationError("Neznámá platba.", code="PAYMENT_NOT_FOUND")
    order = _locked_order(payment.order_id)

    gateway = payment_gateway.get_gateway()
    if gateway.code != provider:
        raise ValidationError("Neznámá platební brána.", code="PAYMENT_NOT_FOUND")
    result = gateway.get_status(provider_payment_id)

    payment.raw_payload = result.raw
    if result.status == payment.status or payment.status == PaymentStatus.REFUNDED:
        payment.save(update_fields=["raw_payload"])
        return PaymentOutcome(payment, order_marked_paid=False)

    if result.status == PaymentStatus.PAID:
        payment.status = PaymentStatus.PAID
        payment.paid_at = timezone.now()
        payment.save()
        return _apply_successful_payment(order, payment)

    payment.status = result.status
    payment.save()
    if result.status == PaymentStatus.FAILED:
        if order.payment_status != PaymentStatus.PAID and not order.payments.filter(status=PaymentStatus.PENDING).exists():
            order.payment_status = PaymentStatus.FAILED
            order.save()
        return PaymentOutcome(payment, order_marked_paid=False)
    return _flag_attention(order, payment, f"Brána oznámila stav platby {result.status} — zkontrolujte ho v bráně.")


# -----------------------------------------------------------------------------
# STAV PLATBY A ZÁSILKY (měnitelné nezávisle na stavu objednávky)
# -----------------------------------------------------------------------------

ALLOWED_PAYMENT_TRANSITIONS = {
    PaymentStatus.PENDING: {PaymentStatus.PAID, PaymentStatus.FAILED},
    PaymentStatus.FAILED: {PaymentStatus.PENDING, PaymentStatus.PAID},
    PaymentStatus.PAID: {PaymentStatus.REFUNDED},
    PaymentStatus.REFUNDED: set(),
}

_SHIPMENT_ORDER = [
    ShipmentStatus.CREATED, ShipmentStatus.LABEL_PRINTED, ShipmentStatus.HANDED_OVER,
    ShipmentStatus.IN_TRANSIT, ShipmentStatus.DELIVERED,
]
_RETURNABLE = {ShipmentStatus.HANDED_OVER, ShipmentStatus.IN_TRANSIT, ShipmentStatus.DELIVERED}


@transaction.atomic
def set_payment_status(order_id, new_status: str, *, actor) -> Order:
    """
    Ruční změna stavu platby BEZ změny stavu objednávky — ten se posouvá zvlášť
    (transition_order). Povolené jsou jen smysluplné přechody a u zrušené nebo
    vrácené objednávky se platba nemění vůbec, aby se nemohly rozejít (zrušená
    objednávka se zaplacenou platbou).

    U ONLINE objednávky stav platby určuje brána, takže `PAID` smí ručně nastavit
    jen ADMIN (např. při výpadku brány). `REFUNDED` smí vždy jen ADMIN.
    """
    order = _locked_order(order_id)
    if new_status not in PaymentStatus.values:
        raise ValidationError("Neplatný stav platby.")
    manual_online_paid = new_status == PaymentStatus.PAID and order.payment_method == PaymentMethod.ONLINE
    if (manual_online_paid or new_status == PaymentStatus.REFUNDED) and not _is_admin(actor):
        raise PermissionDenied("Tuto změnu stavu platby smí provést jen administrátor.")
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        raise InvalidTransition("U zrušené nebo vrácené objednávky už stav platby nejde měnit.")
    if new_status == order.payment_status:
        return order
    if new_status not in ALLOWED_PAYMENT_TRANSITIONS.get(order.payment_status, set()):
        raise InvalidTransition(f"Stav platby {order.payment_status} → {new_status} není povolený.")
    if new_status == PaymentStatus.PAID and order.total is None:
        raise InvalidTransition("Objednávku bez ceny nejde označit jako zaplacenou.")
    order.payment_status = new_status
    order.save()
    return order


@transaction.atomic
def set_shipment_status(order_id, new_status: Optional[str] = None, *, tracking_number: Optional[str] = None) -> Order:
    """
    Změna stavu zásilky a/nebo sledovacího čísla BEZ změny stavu objednávky
    (typicky při tisku štítku nebo předání dopravci). Stav zásilky se může
    jen posouvat dopředu (CREATED → … → DELIVERED), vrácení (RETURNED) jde až od
    předání dopravci. Mění se jen u objednávek ve výrobě a dál; sledovací číslo
    jde opravit kdykoli.
    """
    order = _locked_order(order_id)
    if new_status is not None and new_status != order.shipment_status:
        if new_status not in ShipmentStatus.values:
            raise ValidationError("Neplatný stav zásilky.")
        if order.status not in (OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED):
            raise InvalidTransition("Stav zásilky jde měnit až u objednávky ve výrobě nebo později.")
        current = order.shipment_status
        if new_status == ShipmentStatus.RETURNED:
            allowed = current in _RETURNABLE
        else:
            allowed = (
                current in _SHIPMENT_ORDER
                and _SHIPMENT_ORDER.index(new_status) > _SHIPMENT_ORDER.index(current)
            )
        if not allowed:
            raise InvalidTransition(f"Stav zásilky {current} → {new_status} není povolený.")
        order.shipment_status = new_status
    if tracking_number is not None:
        order.tracking_number = tracking_number.strip()
    order.save()
    return order
