"""
Trikonoš.cz — Django models odpovídající schema_v16.sql (12 tabulek).

Rozdíly oproti čistému SQL, dané Django idiomy (viz Trikonos_DB_implementacni_plan.md):
- Enumy (order_status, payment_method, delivery_type, shipment_status, size,
  staff_role) jsou CharField + choices, ne Postgres ENUM typy — rozhodnutí
  padlo na "Django idiomatické", validace na úrovni aplikace + CHECK constraint.
- `updated_at` se aktualizuje přes `auto_now=True`, ne SQL trigger
  `set_updated_at()` — pokrývá všechny zápisy přes Django ORM (.save()).
  Pokud by něco zapisovalo přímo SQL mimo Django, trigger by to nezachytil;
  pro tenhle projekt to není očekávaný případ.
- order_number_seq (DB sekvence pro bezkolizní čísla objednávek) se vytváří
  v migraci přes RunSQL — viz migrations/0002_order_number_seq.py (ukázka
  níže v komentáři), Django samo sekvence jako pole nenabízí.
- Trigger check_custom_design_placement_product_type (že print_area patří
  ke stejnému product_type jako order_item) zůstává jako SQL trigger přes
  RunSQL v migraci — je to cross-table pravidlo, které Meta.constraints
  nepokryje. Navíc je zrcadlený v CustomDesignPlacement.clean(), aby chyba
  vyskočila už v Django admin/DRF serializeru, ne až při INSERTu do DB.

Umístění: zkopírujte do vašeho Django app (např. shop/models.py).
"""

import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models


# -----------------------------------------------------------------------------
# SPOLEČNÉ ABSTRAKTNÍ ZÁKLADY
# -----------------------------------------------------------------------------

class UUIDModel(models.Model):
    """UUID primární klíč — odpovídá `DEFAULT gen_random_uuid()` v SQL."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class SimpleLookup(UUIDModel):
    """Společný vzor pro jednoduché číselníky: name/position/is_active."""
    name = models.CharField(max_length=255, unique=True)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ["position"]

    def __str__(self):
        return self.name


# -----------------------------------------------------------------------------
# ADMINISTRACE
# -----------------------------------------------------------------------------

class StaffRole(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    STAFF = "STAFF", "Staff"


class UserManager(BaseUserManager):
    """
    Manager pro přihlašování e-mailem. Výchozí UserManager z Djanga vyžaduje
    `username`, který tenhle model nemá — bez tohohle by `create_user()`
    i `manage.py createsuperuser` padaly na TypeError.
    """
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("E-mail je povinný.")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", StaffRole.ADMIN)
        return self._create_user(email, password, **extra)


class User(AbstractUser, UUIDModel):
    """
    Vlastní user model — nahrazuje `users` tabulku ze SQL. Využívá Django
    vestavěné hashování hesla (`password` pole z AbstractUser) místo ručního
    `password_hash`, a Django auth/admin přihlašování funguje bez dalšího kódu.
    """
    username = None  # přihlašujeme se e-mailem, ne username
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=10, choices=StaffRole.choices, default=StaffRole.STAFF)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return self.email


# Nastavte v settings.py: AUTH_USER_MODEL = "shop.User"  (podle názvu vaší app)


# -----------------------------------------------------------------------------
# ČÍSELNÍKY
# -----------------------------------------------------------------------------

class Category(SimpleLookup):
    """Orientační/navigační kategorie na webu (Pánská trička, Kolekce, ...)."""
    slug = models.SlugField(max_length=255, unique=True)

    class Meta(SimpleLookup.Meta):
        db_table = "categories"
        verbose_name_plural = "categories"


class Motif(SimpleLookup):
    """
    Katalogový motiv — přebírá roli bývalých `products`. Zákazník vybírá
    motiv, zbytek (product_type/cut/color/size) si skládá zvlášť na OrderItem.
    """
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True, default="")
    price = models.PositiveIntegerField(help_text="Cena v haléřích")
    # Formát prvku: {"url": "...", "altText": "...", "position": 0, "colorId": "uuid nebo null"}.
    # "colorId" musí odkazovat na Color — validuje se v clean(), ne cizím klíčem (JSONField to neumí).
    images = models.JSONField(default=list, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="motifs")

    class Meta(SimpleLookup.Meta):
        db_table = "motifs"
        constraints = [
            models.CheckConstraint(condition=models.Q(price__gte=0), name="motif_price_non_negative"),
        ]


class Color(SimpleLookup):
    hex_value = models.CharField(max_length=7, help_text="Např. #000000")

    class Meta(SimpleLookup.Meta):
        db_table = "colors"


class Cut(SimpleLookup):
    """Pánské, Dámské, Unisex, Mikina, ..."""

    class Meta(SimpleLookup.Meta):
        db_table = "cuts"


class ProductType(SimpleLookup):
    """Tričko, Mikina, Tílko, Doplněk, ... (dřív garment_types)."""

    class Meta(SimpleLookup.Meta):
        db_table = "product_types"


class PrintArea(SimpleLookup):
    """
    Definovaná místa pro umístění potisku v editoru vlastního potisku.
    Liší se podle typu oblečení — tričko má rukávy, čepice ne.
    """
    product_type = models.ForeignKey(ProductType, on_delete=models.CASCADE, related_name="print_areas")
    width_mm = models.PositiveIntegerField(help_text="Rozměr tiskové plochy pro editor i tiskárnu")
    height_mm = models.PositiveIntegerField()

    class Meta(SimpleLookup.Meta):
        db_table = "print_areas"
        constraints = [
            models.UniqueConstraint(fields=["product_type", "name"], name="uq_print_area_product_type_name"),
            models.CheckConstraint(condition=models.Q(width_mm__gt=0), name="print_area_width_positive"),
            models.CheckConstraint(condition=models.Q(height_mm__gt=0), name="print_area_height_positive"),
        ]


class Carrier(SimpleLookup):
    """
    Dopravci. Cena dopravy na dopravci nezávisí (jedna cena pro celý e-shop,
    viz ShippingSettings a ShippingQuantityDiscount) — dopravce je jen
    logistická volba. Bez supports_pickup_point/supports_home_delivery.
    """
    code = models.CharField(max_length=50, unique=True, help_text="Interní kód, např. PACKETA")

    class Meta(SimpleLookup.Meta):
        db_table = "carriers"


class ShippingSettings(models.Model):
    """
    Základní cena dopravy pro celý e-shop (jediný řádek, id = 1) — nezávisle
    na dopravci a typu doručení. Slevy podle počtu kusů jsou v
    ShippingQuantityDiscount.

    `price = NULL` znamená "nenastaveno". Cena dopravy musí být ale vždy známá,
    takže dokud není nastavená, služby objednávku odmítnou
    (ShippingPriceNotConfigured) a frontend nemá co zobrazit — nastavení je
    povinný krok při zprovoznění e-shopu.
    """
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    price = models.PositiveIntegerField(null=True, blank=True, help_text="Cena v haléřích; 0 = doprava vždy zdarma")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shipping_settings"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="shipping_settings_singleton"),
        ]

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        return obj


class ShippingQuantityDiscount(UUIDModel):
    """
    Slevový práh dopravy podle CELKOVÉHO počtu kusů v objednávce (součet
    `quantity` přes všechny položky). Platí práh s nejvyšším `min_quantity`,
    který objednávka splňuje; bez shody platí plná cena. 100 % = doprava zdarma.
    """
    min_quantity = models.PositiveIntegerField(unique=True, help_text="Od kolika kusů celkem práh platí (včetně)")
    discount_percent = models.PositiveSmallIntegerField(help_text="0–100; 100 = doprava zdarma")
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "shipping_quantity_discounts"
        ordering = ["min_quantity"]
        constraints = [
            models.CheckConstraint(condition=models.Q(min_quantity__gte=1), name="shipping_discount_min_quantity_positive"),
            models.CheckConstraint(condition=models.Q(discount_percent__lte=100), name="shipping_discount_percent_max_100"),
        ]

    def __str__(self):
        return f"od {self.min_quantity} ks: −{self.discount_percent} %"


# -----------------------------------------------------------------------------
# KLIENTI
# -----------------------------------------------------------------------------

class Customer(UUIDModel):
    """
    Evidence zákazníků pro admin (seznam, historie objednávek podle e-mailu).
    Bez hesla/účtu — čistě kontaktní záznam dohledaný/založený podle e-mailu.
    """
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=30, blank=True, default="")
    first_name = models.CharField(max_length=255, blank=True, default="")
    last_name = models.CharField(max_length=255, blank=True, default="")
    company_name = models.CharField(max_length=255, blank=True, default="", help_text="Pro B2B/týmové poptávky")
    marketing_consent = models.BooleanField(default=False)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customers"

    def __str__(self):
        return self.email


# -----------------------------------------------------------------------------
# OBJEDNÁVKY (včetně dopravy, platby a obou adres)
# -----------------------------------------------------------------------------

class OrderStatus(models.TextChoices):
    QUOTE_REQUESTED = "QUOTE_REQUESTED", "Čeká na cenu od admina"
    NEW = "NEW", "Nová"
    PAID = "PAID", "Zaplaceno"
    PROCESSING = "PROCESSING", "Zpracovává se"
    SHIPPED = "SHIPPED", "Odesláno"
    DELIVERED = "DELIVERED", "Doručeno"
    CANCELLED = "CANCELLED", "Zrušeno"
    REFUNDED = "REFUNDED", "Vráceno"


class DeliveryType(models.TextChoices):
    PICKUP_POINT = "PICKUP_POINT", "Výdejní místo"
    HOME_DELIVERY = "HOME_DELIVERY", "Doručení domů"


class PaymentMethod(models.TextChoices):
    """Platba je jen online přes platební bránu, nebo dobírka (převod a faktura odpadají)."""
    ONLINE = "ONLINE", "Online platba (platební brána)"
    CASH_ON_DELIVERY = "CASH_ON_DELIVERY", "Dobírka"


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", "Čeká na platbu"
    PAID = "PAID", "Zaplaceno"
    FAILED = "FAILED", "Selhalo"
    REFUNDED = "REFUNDED", "Vráceno"


class ShipmentStatus(models.TextChoices):
    CREATED = "CREATED", "Vytvořeno"
    LABEL_PRINTED = "LABEL_PRINTED", "Štítek vytištěn"
    HANDED_OVER = "HANDED_OVER", "Předáno dopravci"
    IN_TRANSIT = "IN_TRANSIT", "Na cestě"
    DELIVERED = "DELIVERED", "Doručeno"
    RETURNED = "RETURNED", "Vráceno"


class Order(UUIDModel):
    """
    Nese identitu, cenu, dopravu, platbu i obě adresy v jedné tabulce —
    vědomý kompromis pro jednoduchost (žádný JOIN na zobrazení objednávky),
    viz Trikonos_DB_implementacni_plan.md, sekce 18.
    """
    order_number = models.CharField(max_length=50, unique=True, help_text="Generováno z order_number_seq")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.NEW, db_index=True)

    # Ceny — nullable, dokud není stanovená (vlastní potisk čeká na admina)
    subtotal = models.PositiveIntegerField(null=True, blank=True, help_text="V haléřích")
    shipping_cost = models.PositiveIntegerField(null=True, blank=True)
    shipping_cost_manual = models.BooleanField(
        default=False,
        help_text="True = cenu dopravy nastavil ručně admin; automaticky se pak nepřepočítává při změně počtu kusů",
    )
    total = models.PositiveIntegerField(null=True, blank=True)
    confirmed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Kdy byla objednávka potvrzena zákazníkovi (cena + platební instrukce). Podmínka pro zahájení výroby. "
                  "Změna ceny potvrzení zruší.",
    )
    currency = models.CharField(max_length=3, default="CZK")

    # Doprava
    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT, null=True, blank=True, related_name="orders")
    delivery_type = models.CharField(max_length=20, choices=DeliveryType.choices, null=True, blank=True)
    pickup_point_id = models.CharField(max_length=100, blank=True, default="")
    pickup_point_name = models.CharField(max_length=255, blank=True, default="")
    tracking_number = models.CharField(max_length=100, blank=True, default="")
    shipment_status = models.CharField(max_length=20, choices=ShipmentStatus.choices, default=ShipmentStatus.CREATED)

    # Platba
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, null=True, blank=True)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)

    # Fakturační adresa
    billing_full_name = models.CharField(max_length=255)
    billing_company_name = models.CharField(max_length=255, blank=True, default="")
    billing_ico = models.CharField(max_length=20, blank=True, default="")
    billing_dic = models.CharField(max_length=20, blank=True, default="")
    billing_street = models.CharField(max_length=255)
    billing_city = models.CharField(max_length=255)
    billing_zip = models.CharField(max_length=20)
    billing_country = models.CharField(max_length=2, default="CZ")

    # Doručovací adresa (aplikace předvyplní stejnými hodnotami, pokud je totožná s fakturační)
    shipping_full_name = models.CharField(max_length=255)
    shipping_company_name = models.CharField(max_length=255, blank=True, default="")
    shipping_street = models.CharField(max_length=255)
    shipping_city = models.CharField(max_length=255)
    shipping_zip = models.CharField(max_length=20)
    shipping_country = models.CharField(max_length=2, default="CZ")

    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        constraints = [
            models.CheckConstraint(condition=models.Q(subtotal__isnull=True) | models.Q(subtotal__gte=0), name="order_subtotal_non_negative"),
            models.CheckConstraint(condition=models.Q(shipping_cost__isnull=True) | models.Q(shipping_cost__gte=0), name="order_shipping_cost_non_negative"),
            models.CheckConstraint(condition=models.Q(total__isnull=True) | models.Q(total__gte=0), name="order_total_non_negative"),
        ]

    def __str__(self):
        return self.order_number


class Payment(UUIDModel):
    """
    Jeden POKUS o online platbu přes platební bránu. Objednávka jich může mít
    víc (nezdařený pokus, opakování, starý pokus nahrazený novým po změně ceny).
    Dobírka žádné řádky nemá — platí se dopravci a stav drží jen Order.payment_status.

    `provider` je kód brány (text, např. "gopay"), ne enum — výměna brány tak
    nevyžaduje migraci. `(provider, provider_payment_id)` je unikátní, aby
    opakovaná notifikace od brány nikdy nezaložila druhý záznam.
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    provider = models.CharField(max_length=30, help_text="Kód platební brány, např. 'gopay'")
    provider_payment_id = models.CharField(max_length=100, help_text="ID platby u brány")
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    amount = models.PositiveIntegerField(help_text="Částka pokusu v haléřích (= order.total v okamžiku vzniku)")
    redirect_url = models.URLField(max_length=1000, blank=True, default="", help_text="Adresa platební stránky brány")
    raw_payload = models.JSONField(null=True, blank=True, help_text="Poslední odpověď brány o stavu platby")
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payments"
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_payment_id"], name="uq_payment_provider_payment_id"),
        ]

    def __str__(self):
        return f"{self.provider}:{self.provider_payment_id} ({self.status})"


SIZE_CHOICES = [(s, s) for s in ("S", "M", "L", "XL", "XXL")]


class OrderItem(UUIDModel):
    """
    Jedna objednaná položka (jedno tričko/mikina/doplněk). Dvě cesty, které
    se nikdy nemíchají v jedné položce:
      - is_custom=False → motif (vždy jeden) z katalogu, cena = motif.price
      - is_custom=True  → custom_text + CustomDesignPlacement řádky (víc
        umístění možných), cenu vždy nastavuje ručně admin
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    is_custom = models.BooleanField(default=False)
    motif = models.ForeignKey(Motif, on_delete=models.PROTECT, null=True, blank=True, related_name="order_items")
    custom_text = models.TextField(blank=True, default="", help_text="Volné instrukce k potisku, jen pro is_custom=True")

    product_type = models.ForeignKey(ProductType, on_delete=models.PROTECT, related_name="order_items")
    cut = models.ForeignKey(Cut, on_delete=models.PROTECT, related_name="order_items")
    color = models.ForeignKey(Color, on_delete=models.PROTECT, related_name="order_items")
    size = models.CharField(max_length=3, choices=SIZE_CHOICES)

    item_label = models.CharField(max_length=255, help_text='Snapshot, např. "Bitcoin / Pánské / Černá / M"')
    unit_price = models.PositiveIntegerField(null=True, blank=True, help_text="motif.price (katalog) nebo NULL, dokud admin necení custom")
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "order_items"
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="order_item_quantity_positive"),
            models.CheckConstraint(
                condition=(
                    (models.Q(is_custom=False) & models.Q(motif__isnull=False))
                    | (models.Q(is_custom=True) & models.Q(motif__isnull=True))
                ),
                name="order_item_custom_xor_motif",
            ),
        ]

    def clean(self):
        # Zrcadlí CHECK constraint, aby chyba vyskočila v Django admin/DRF
        # serializeru dřív, než se vůbec zkusí uložit do DB.
        if self.is_custom and self.motif_id:
            raise ValidationError("Custom položka nesmí mít přiřazený katalogový motiv.")
        if not self.is_custom and not self.motif_id:
            raise ValidationError("Katalogová položka musí mít přiřazený motiv.")

    def __str__(self):
        return self.item_label


class CustomDesignPlacement(UUIDModel):
    """
    Umístění vlastního potisku — relační řádky (ne JSONB), aby print_area byl
    opravdový cizí klíč a šly dělat admin reporty napříč objednávkami (např.
    "všechny potisky na záda tento týden"). Jedna custom položka může mít
    víc řádků zde (víc obrázků/nápisů na různých blocích jednoho trička).
    """
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="design_placements")
    print_area = models.ForeignKey(PrintArea, on_delete=models.PROTECT, related_name="placements")
    url = models.URLField(max_length=500, help_text="Nahraný soubor od zákazníka")
    x = models.IntegerField(default=0, help_text="Pozice v rámci bloku, ve stejné jednotce jako print_area.width_mm/height_mm")
    y = models.IntegerField(default=0)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    rotation_deg = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "custom_design_placements"
        constraints = [
            models.CheckConstraint(condition=models.Q(width__gt=0), name="placement_width_positive"),
            models.CheckConstraint(condition=models.Q(height__gt=0), name="placement_height_positive"),
        ]

    def clean(self):
        # Zrcadlí SQL trigger check_custom_design_placement_product_type —
        # cross-table pravidlo, které Meta.constraints nepokryje. DB trigger
        # (viz migrace) zůstává jako poslední pojistka, tohle chybu ukáže dřív.
        if self.order_item_id and self.print_area_id:
            if self.order_item.product_type_id != self.print_area.product_type_id:
                raise ValidationError(
                    "print_area patří k jinému typu oblečení, než má order_item."
                )
