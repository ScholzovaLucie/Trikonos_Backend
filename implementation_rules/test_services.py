"""
Testy business logiky objednávání (services.py, notifications.py, design_files.py).

Spuštění v Django projektu:  python manage.py test shop
"""

import uuid
from unittest import mock

from django.core import mail, signing
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import TestCase, override_settings

from . import design_files, order_access, services
from .models import (
    Carrier, Category, Color, Customer, Cut, DeliveryType, Motif, Order, OrderStatus,
    Payment, PaymentMethod, PaymentStatus, PrintArea, ProductType, ShipmentStatus,
    ShippingQuantityDiscount, ShippingSettings, StaffRole, User,
)
from .payment_gateway import DummyGateway
from .services import (
    AddressInput, ContactInput, DeliveryInput, InvalidTransition, ItemInput, OrderLocked,
    PlacementInput, ShippingPriceNotConfigured,
)

# Ceny v haléřích. Doprava: základ 99 Kč; od 3 ks −50 %; od 5 ks zdarma.
MOTIF = 54900          # Bitcoin 549 Kč
CHEAP = 10000          # Lanovka 100 Kč
SHIPPING_BASE = 9900
SHIPPING_3_PCS = 4950


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],        # rychlé hashování jen v testech
    TRIKONOS_PAYMENT_GATEWAY=f"{DummyGateway.__module__}.DummyGateway",        # falešná brána, žádná skutečná platba
    TRIKONOS_ORDER_URL_TEMPLATE="https://trikonos.cz/objednavka/{token}",
)
class OrderTestBase(TestCase):
    def setUp(self):
        DummyGateway.reset()

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Hory", slug="hory")
        cls.motif = Motif.objects.create(name="Bitcoin", slug="bitcoin", price=MOTIF, category=cls.category)
        cls.cheap_motif = Motif.objects.create(name="Lanovka", slug="lanovka", price=CHEAP, category=cls.category)
        cls.color = Color.objects.create(name="Černá", hex_value="#000000")
        cls.cut = Cut.objects.create(name="Pánské")
        cls.tshirt = ProductType.objects.create(name="Tričko")
        cls.cap = ProductType.objects.create(name="Čepice")
        cls.front = PrintArea.objects.create(product_type=cls.tshirt, name="Přední strana", width_mm=300, height_mm=400)
        cls.back = PrintArea.objects.create(product_type=cls.tshirt, name="Záda", width_mm=300, height_mm=400)
        cls.cap_front = PrintArea.objects.create(product_type=cls.cap, name="Čelo", width_mm=100, height_mm=50)
        cls.carrier = Carrier.objects.create(name="Zásilkovna", code="PACKETA")
        cls.admin = User.objects.create_user(email="admin@trikonos.cz", password="x", name="Admin", role=StaffRole.ADMIN)
        cls.staff = User.objects.create_user(email="staff@trikonos.cz", password="x", name="Staff", role=StaffRole.STAFF)

        ShippingSettings.objects.update_or_create(id=1, defaults={"price": SHIPPING_BASE})
        cls.discount_3 = ShippingQuantityDiscount.objects.create(min_quantity=3, discount_percent=50)
        cls.discount_5 = ShippingQuantityDiscount.objects.create(min_quantity=5, discount_percent=100)

    # -- pomocníci --
    def token(self, url="https://x.cz/logo.png"):
        return design_files.make_file_token(url)

    def contact(self, email="jan@example.com", **kw):
        return ContactInput(email=email, **kw)

    def address(self):
        return AddressInput(full_name="Jan Novák", street="Ulice 1", city="Praha", zip="11000")

    def delivery(self, **kw):
        defaults = dict(delivery_type=DeliveryType.HOME_DELIVERY, carrier_id=self.carrier.id)
        defaults.update(kw)
        return DeliveryInput(**defaults)

    def catalog_item(self, motif=None, quantity=1, **kw):
        return ItemInput(
            product_type_id=self.tshirt.id, cut_id=self.cut.id, color_id=self.color.id,
            size="M", quantity=quantity, motif_id=(motif or self.motif).id, **kw,
        )

    def placement(self, area=None, **kw):
        defaults = dict(print_area_id=(area or self.front).id, file_token=self.token(), x=10, y=10, width=100, height=100)
        defaults.update(kw)
        return PlacementInput(**defaults)

    def custom_item(self, placements=None, product_type=None, quantity=1, **kw):
        pt = product_type or self.tshirt
        area = self.front if pt == self.tshirt else self.cap_front
        return ItemInput(
            product_type_id=pt.id, cut_id=self.cut.id, color_id=self.color.id, size="L",
            quantity=quantity, custom_text="Menší logo na hrudi",
            placements=placements if placements is not None else [self.placement(area)], **kw,
        )

    def make_order(self, items, payment_method=PaymentMethod.ONLINE, **kw):
        kw.setdefault("delivery", self.delivery())
        if not kw.get("by_admin"):
            kw.setdefault("terms_accepted", True)
        return services.create_order(
            contact=self.contact(), billing=self.address(), shipping=self.address(),
            items=items, payment_method=payment_method, **kw,
        )

    def admin_order(self, items, payment_method=PaymentMethod.CASH_ON_DELIVERY, **kw):
        return self.make_order(items, payment_method=payment_method, by_admin=True, **kw)

    def pay_online(self, order):
        """Zákazník zaplatí u brány a brána o tom pošle notifikaci."""
        payment = services.start_online_payment(order.id, return_url="https://trikonos.cz/dekujeme")
        DummyGateway.statuses[payment.provider_payment_id] = PaymentStatus.PAID
        return services.handle_payment_notification(payment.provider, payment.provider_payment_id)


# -----------------------------------------------------------------------------
# CENA DOPRAVY
# -----------------------------------------------------------------------------

class ShippingCalculationTests(OrderTestBase):
    def test_full_price_below_first_threshold(self):
        self.assertEqual(services.calculate_shipping_cost(1), SHIPPING_BASE)
        self.assertEqual(services.calculate_shipping_cost(2), SHIPPING_BASE)

    def test_threshold_applies_from_its_quantity(self):
        self.assertEqual(services.calculate_shipping_cost(3), SHIPPING_3_PCS)
        self.assertEqual(services.calculate_shipping_cost(4), SHIPPING_3_PCS)

    def test_highest_matching_threshold_wins(self):
        self.assertEqual(services.calculate_shipping_cost(5), 0)
        self.assertEqual(services.calculate_shipping_cost(50), 0)

    def test_inactive_threshold_is_ignored(self):
        self.discount_5.is_active = False
        self.discount_5.save()
        self.assertEqual(services.calculate_shipping_cost(5), SHIPPING_3_PCS)

    def test_rounds_half_a_haler_up(self):
        ShippingSettings.objects.filter(id=1).update(price=9901)
        self.assertEqual(services.calculate_shipping_cost(3), 4951)   # 4950,5 → 4951

    def test_zero_base_price_means_always_free(self):
        ShippingSettings.objects.filter(id=1).update(price=0)
        self.assertEqual(services.calculate_shipping_cost(1), 0)

    def test_no_thresholds_means_full_price_always(self):
        ShippingQuantityDiscount.objects.all().delete()
        self.assertEqual(services.calculate_shipping_cost(100), SHIPPING_BASE)

    def test_unset_price_is_a_configuration_error(self):
        ShippingSettings.objects.filter(id=1).update(price=None)
        with self.assertRaises(ShippingPriceNotConfigured):
            services.calculate_shipping_cost(1)

    def test_missing_settings_row_is_also_unconfigured(self):
        ShippingSettings.objects.all().delete()
        with self.assertRaises(ShippingPriceNotConfigured):
            services.calculate_shipping_cost(1)

    def test_threshold_must_be_unique_per_quantity(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            ShippingQuantityDiscount.objects.create(min_quantity=3, discount_percent=10)


# -----------------------------------------------------------------------------
# VYTVOŘENÍ KATALOGOVÉ OBJEDNÁVKY
# -----------------------------------------------------------------------------

class CreateCatalogOrderTests(OrderTestBase):
    def test_catalog_order_is_priced_new_and_confirmed(self):
        order = self.make_order([self.catalog_item(quantity=2)])
        self.assertEqual(order.status, OrderStatus.NEW)
        self.assertEqual(order.subtotal, 2 * MOTIF)
        self.assertEqual(order.shipping_cost, SHIPPING_BASE)
        self.assertEqual(order.total, 2 * MOTIF + SHIPPING_BASE)
        self.assertIsNotNone(order.confirmed_at)
        self.assertFalse(order.shipping_cost_manual)

    def test_shipping_uses_total_quantity_across_items(self):
        order = self.make_order([self.catalog_item(self.motif, 2), self.catalog_item(self.cheap_motif, 1)])
        self.assertEqual(order.shipping_cost, SHIPPING_3_PCS)          # 3 kusy celkem

    def test_free_shipping_from_five_pieces(self):
        order = self.make_order([self.catalog_item(quantity=5)])
        self.assertEqual(order.shipping_cost, 0)
        self.assertEqual(order.total, order.subtotal)

    def test_item_price_is_snapshot(self):
        order = self.make_order([self.catalog_item()])
        self.motif.price = 99900
        self.motif.save()
        self.assertEqual(order.items.get().unit_price, MOTIF)

    def test_item_label_and_flags(self):
        item = self.make_order([self.catalog_item()]).items.get()
        self.assertFalse(item.is_custom)
        self.assertEqual(item.item_label, "Bitcoin / Tričko / Pánské / Černá / M")

    def test_payment_method_required(self):
        with self.assertRaisesMessage(ValidationError, "způsob platby"):
            self.make_order([self.catalog_item()], payment_method=None)

    def test_terms_must_be_accepted(self):
        with self.assertRaises(ValidationError) as cm:
            self.make_order([self.catalog_item()], terms_accepted=False)
        self.assertEqual(cm.exception.code, "TERMS_NOT_ACCEPTED")
        self.assertEqual(Order.objects.count(), 0)

    def test_inactive_motif_rejected(self):
        self.motif.is_active = False
        self.motif.save()
        with self.assertRaisesMessage(ValidationError, "Motiv"):
            self.make_order([self.catalog_item()])

    def test_empty_cart_rejected(self):
        with self.assertRaises(ValidationError):
            self.make_order([])

    def test_invalid_size_and_quantity_rejected(self):
        item = self.catalog_item()
        item.size = "XXXL"
        with self.assertRaisesMessage(ValidationError, "velikost"):
            self.make_order([item])
        for bad in (0, 501):
            with self.assertRaisesMessage(ValidationError, "Množství"):
                self.make_order([self.catalog_item(quantity=bad)])

    def test_pickup_point_requires_id_and_name(self):
        for kw in (dict(), dict(pickup_point_id="Z1"), dict(pickup_point_name="Praha 1")):
            delivery = self.delivery(delivery_type=DeliveryType.PICKUP_POINT, **kw)
            with self.assertRaisesMessage(ValidationError, "výdejní místo"):
                self.make_order([self.catalog_item()], delivery=delivery)
        ok = self.delivery(delivery_type=DeliveryType.PICKUP_POINT, pickup_point_id="Z1", pickup_point_name="Praha 1")
        self.assertEqual(self.make_order([self.catalog_item()], delivery=ok).pickup_point_id, "Z1")

    def test_carrier_is_required_and_must_be_active(self):
        with self.assertRaisesMessage(ValidationError, "dopravce"):
            self.make_order([self.catalog_item()], delivery=self.delivery(carrier_id=None))
        self.carrier.is_active = False
        self.carrier.save()
        with self.assertRaisesMessage(ValidationError, "Dopravce"):
            self.make_order([self.catalog_item()])

    def test_carrier_does_not_change_the_price(self):
        dpd = Carrier.objects.create(name="DPD", code="DPD")
        a = self.make_order([self.catalog_item()])
        b = self.make_order([self.catalog_item()], delivery=self.delivery(carrier_id=dpd.id))
        self.assertEqual(a.shipping_cost, b.shipping_cost)

    def test_catalog_item_with_placements_rejected(self):
        item = self.catalog_item()
        item.placements = [self.placement()]
        with self.assertRaises(ValidationError):
            self.make_order([item])

    def test_customer_cannot_set_prices(self):
        """Ceny z veřejného checkoutu se ignorují — zákazník si cenu nastavit nemůže."""
        order = self.make_order(
            [self.catalog_item(unit_price=1), self.custom_item(unit_price=1)], shipping_cost=0,
        )
        prices = {i.is_custom: i.unit_price for i in order.items.all()}
        self.assertEqual(prices, {False: MOTIF, True: None})
        self.assertEqual(order.shipping_cost, SHIPPING_BASE)           # ne 0
        self.assertFalse(order.shipping_cost_manual)
        self.assertEqual(order.status, OrderStatus.QUOTE_REQUESTED)

    def test_duplicate_submit_returns_same_order(self):
        order_id = uuid.uuid4()
        first = self.make_order([self.catalog_item()], order_id=order_id)
        second = self.make_order([self.catalog_item()], order_id=order_id)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Order.objects.count(), 1)

    def test_order_numbers_are_unique(self):
        a = self.make_order([self.catalog_item()])
        b = self.make_order([self.catalog_item()])
        self.assertNotEqual(a.order_number, b.order_number)

    def test_unconfigured_shipping_rejects_order_and_saves_nothing(self):
        ShippingSettings.objects.filter(id=1).update(price=None)
        with self.assertRaises(ShippingPriceNotConfigured):
            self.make_order([self.catalog_item()])
        self.assertEqual((Order.objects.count(), Customer.objects.count()), (0, 0))

    @override_settings(TRIKONOS_AUTO_CONFIRM_CATALOG_ORDERS=False)
    def test_auto_confirmation_can_be_turned_off(self):
        order = self.make_order([self.catalog_item()])
        self.assertEqual(order.status, OrderStatus.NEW)
        self.assertIsNone(order.confirmed_at)


# -----------------------------------------------------------------------------
# VLASTNÍ POTISK
# -----------------------------------------------------------------------------

class CreateCustomOrderTests(OrderTestBase):
    def test_custom_order_waits_for_quote_but_shipping_is_known(self):
        order = self.make_order([self.custom_item()])
        self.assertEqual(order.status, OrderStatus.QUOTE_REQUESTED)
        self.assertIsNone(order.subtotal)
        self.assertIsNone(order.total)
        self.assertEqual(order.shipping_cost, SHIPPING_BASE)           # závisí jen na počtu kusů
        self.assertIsNone(order.confirmed_at)
        item = order.items.get()
        self.assertTrue(item.is_custom)
        self.assertIsNone(item.unit_price)
        self.assertEqual(item.design_placements.count(), 1)

    def test_shipping_of_quote_follows_quantity(self):
        order = self.make_order([self.custom_item(quantity=3)])
        self.assertEqual(order.shipping_cost, SHIPPING_3_PCS)

    def test_file_address_comes_from_the_token(self):
        placement = self.placement(file_token=self.token("https://cdn.trikonos.cz/uploads/abc.png"))
        order = self.make_order([self.custom_item([placement])])
        self.assertEqual(order.items.get().design_placements.get().url, "https://cdn.trikonos.cz/uploads/abc.png")

    def test_multiple_placements_on_one_item(self):
        placements = [self.placement(self.front, x=0, y=0), self.placement(self.back, x=50, y=50, width=200, height=200, rotation_deg=15)]
        order = self.make_order([self.custom_item(placements)])
        self.assertEqual(order.items.get().design_placements.count(), 2)

    def test_custom_item_needs_a_placement(self):
        with self.assertRaisesMessage(ValidationError, "aspoň jedno umístění"):
            self.make_order([self.custom_item(placements=[])])

    def test_garbage_token_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            self.make_order([self.custom_item([self.placement(file_token="nesmysl")])])
        self.assertEqual(cm.exception.code, "INVALID_FILE_TOKEN")

    def test_expired_token_rejected(self):
        item = self.custom_item()
        with mock.patch.object(design_files, "MAX_AGE", -1):
            with self.assertRaises(ValidationError) as cm:
                self.make_order([item])
        self.assertEqual(cm.exception.code, "INVALID_FILE_TOKEN")

    def test_token_signed_elsewhere_rejected(self):
        forged = signing.dumps({"url": "https://evil.example/x.png"}, salt="jina-sul")
        with self.assertRaises(ValidationError):
            self.make_order([self.custom_item([self.placement(file_token=forged)])])

    def test_placement_exceeding_area_rejected(self):
        with self.assertRaisesMessage(ValidationError, "přesahuje"):
            self.make_order([self.custom_item([self.placement(x=250, width=100)])])   # 250+100 > 300

    def test_placement_with_negative_or_zero_size_rejected(self):
        for kw in (dict(x=-5), dict(y=-1), dict(width=0), dict(height=-3)):
            with self.assertRaises(ValidationError):
                self.make_order([self.custom_item([self.placement(**kw)])])

    def test_area_of_other_product_type_rejected(self):
        wrong = self.placement(self.cap_front, x=0, y=0, width=50, height=20)
        with self.assertRaisesMessage(ValidationError, "nepatří k typu oblečení"):
            self.make_order([self.custom_item([wrong])])

    def test_mixed_order_waits_for_quote_but_keeps_catalog_price(self):
        order = self.make_order([self.catalog_item(), self.custom_item()])
        self.assertEqual(order.status, OrderStatus.QUOTE_REQUESTED)
        self.assertIsNone(order.total)
        prices = {i.is_custom: i.unit_price for i in order.items.all()}
        self.assertEqual(prices, {False: MOTIF, True: None})
        self.assertEqual(order.shipping_cost, SHIPPING_BASE)           # 2 kusy


# -----------------------------------------------------------------------------
# OBJEDNÁVKA ZALOŽENÁ ADMINEM (telefon / e-mail)
# -----------------------------------------------------------------------------

class AdminCreatedOrderTests(OrderTestBase):
    def test_priced_custom_item_gives_new_unconfirmed_order(self):
        order = self.admin_order([self.custom_item(unit_price=45000)], terms_accepted=False)
        self.assertEqual(order.status, OrderStatus.NEW)
        self.assertEqual(order.subtotal, 45000)
        self.assertEqual(order.total, 45000 + SHIPPING_BASE)
        self.assertIsNone(order.confirmed_at)                           # potvrzuje admin ručně

    def test_unpriced_custom_item_still_waits_for_quote(self):
        order = self.admin_order([self.custom_item()])
        self.assertEqual(order.status, OrderStatus.QUOTE_REQUESTED)

    def test_manual_shipping_cost(self):
        order = self.admin_order([self.custom_item(unit_price=45000)], shipping_cost=1000)
        self.assertEqual(order.shipping_cost, 1000)
        self.assertTrue(order.shipping_cost_manual)
        self.assertEqual(order.total, 46000)

    def test_admin_can_override_catalog_price(self):
        order = self.admin_order([self.catalog_item(unit_price=40000)])
        self.assertEqual(order.items.get().unit_price, 40000)

    def test_negative_price_rejected(self):
        with self.assertRaises(ValidationError):
            self.admin_order([self.custom_item(unit_price=-1)])
        with self.assertRaises(ValidationError):
            self.admin_order([self.custom_item(unit_price=100)], shipping_cost=-5)

    def test_same_validation_as_public_checkout(self):
        item = self.custom_item([self.placement(x=250, width=100)])
        with self.assertRaisesMessage(ValidationError, "přesahuje"):
            self.admin_order([item])


# -----------------------------------------------------------------------------
# OVĚŘENÍ KOŠÍKU
# -----------------------------------------------------------------------------

class CartValidationTests(OrderTestBase):
    def test_catalog_cart_is_fully_priced(self):
        cart = services.validate_cart([self.catalog_item(quantity=2)])
        line = cart.items[0]
        self.assertEqual((line.valid, line.errors, line.unit_price, line.line_total), (True, [], MOTIF, 2 * MOTIF))
        self.assertEqual(line.item_label, "Bitcoin / Tričko / Pánské / Černá / M")
        self.assertEqual(cart.subtotal, 2 * MOTIF)
        self.assertEqual(cart.shipping_cost, SHIPPING_BASE)
        self.assertEqual(cart.total, 2 * MOTIF + SHIPPING_BASE)
        self.assertFalse(cart.requires_quote)

    def test_custom_cart_requires_quote_but_shipping_is_known(self):
        cart = services.validate_cart([self.custom_item(quantity=3)])
        self.assertTrue(cart.requires_quote)
        self.assertIsNone(cart.items[0].unit_price)
        self.assertIsNone(cart.subtotal)
        self.assertIsNone(cart.total)
        self.assertEqual(cart.shipping_cost, SHIPPING_3_PCS)

    def test_mixed_cart_has_no_subtotal(self):
        cart = services.validate_cart([self.catalog_item(), self.custom_item()])
        self.assertTrue(cart.requires_quote)
        self.assertEqual(cart.items[0].line_total, MOTIF)
        self.assertIsNone(cart.subtotal)

    def test_invalid_line_reports_errors_and_blocks_totals(self):
        self.cheap_motif.is_active = False
        self.cheap_motif.save()
        cart = services.validate_cart([self.catalog_item(), self.catalog_item(self.cheap_motif)])
        self.assertTrue(cart.items[0].valid)
        bad = cart.items[1]
        self.assertFalse(bad.valid)
        self.assertTrue(any("Motiv" in e for e in bad.errors))
        self.assertIsNone(bad.item_label)
        self.assertEqual((cart.subtotal, cart.shipping_cost, cart.total), (None, None, None))

    def test_placement_errors_are_reported_per_line(self):
        cart = services.validate_cart([self.custom_item([self.placement(x=250, width=100)])])
        self.assertFalse(cart.items[0].valid)
        self.assertTrue(any("přesahuje" in e for e in cart.items[0].errors))

    def test_shipping_discount_follows_total_quantity(self):
        cart = services.validate_cart([self.catalog_item(quantity=2), self.catalog_item(self.cheap_motif)])
        self.assertEqual(cart.shipping_cost, SHIPPING_3_PCS)

    def test_price_shown_equals_price_charged(self):
        """Nejdůležitější: košík a skutečné naúčtování se nikdy nerozejdou."""
        for items in ([self.catalog_item()], [self.catalog_item(quantity=3)], [self.catalog_item(quantity=5)]):
            cart = services.validate_cart(items)
            order = self.make_order(items)
            self.assertEqual((cart.subtotal, cart.shipping_cost, cart.total),
                             (order.subtotal, order.shipping_cost, order.total))

    def test_client_cannot_influence_price(self):
        cart = services.validate_cart([self.catalog_item(unit_price=1), self.custom_item(unit_price=1)])
        self.assertEqual(cart.items[0].unit_price, MOTIF)
        self.assertIsNone(cart.items[1].unit_price)

    def test_empty_cart_rejected(self):
        with self.assertRaisesMessage(ValidationError, "prázdný"):
            services.validate_cart([])

    def test_unconfigured_shipping_is_loud(self):
        ShippingSettings.objects.filter(id=1).update(price=None)
        with self.assertRaises(ShippingPriceNotConfigured):
            services.validate_cart([self.catalog_item()])

    def test_nothing_is_saved(self):
        services.validate_cart([self.catalog_item(), self.custom_item()])
        self.assertEqual((Order.objects.count(), Customer.objects.count()), (0, 0))


# -----------------------------------------------------------------------------
# OCENĚNÍ, ÚPRAVY A POTVRZENÍ
# -----------------------------------------------------------------------------

class PricingAndConfirmationTests(OrderTestBase):
    def quote(self, items=None, **kw):
        return self.make_order(items or [self.custom_item()], payment_method=PaymentMethod.CASH_ON_DELIVERY, **kw)

    def priced_quote(self, price=45000, **kw):
        order = self.quote(**kw)
        return services.price_item(order.id, order.items.get().id, unit_price=price)

    def test_admin_prices_item_without_confirming(self):
        order = self.quote()
        with self.captureOnCommitCallbacks(execute=True):
            priced = services.price_item(order.id, order.items.get().id, unit_price=45000)
        self.assertEqual(priced.status, OrderStatus.QUOTE_REQUESTED)    # stále poptávka
        self.assertEqual((priced.subtotal, priced.shipping_cost, priced.total), (45000, SHIPPING_BASE, 45000 + SHIPPING_BASE))
        self.assertIsNone(priced.confirmed_at)
        self.assertEqual(len(mail.outbox), 0)                           # ocenění nic neposílá

    def test_confirm_moves_quote_to_new_and_sets_confirmation(self):
        order = self.priced_quote()
        with self.captureOnCommitCallbacks(execute=True):
            confirmed = services.confirm_order(order.id)
        self.assertEqual(confirmed.status, OrderStatus.NEW)
        self.assertIsNotNone(confirmed.confirmed_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("potvrzena", mail.outbox[0].subject)

    def test_confirm_requires_a_price(self):
        order = self.quote()
        with self.assertRaises(ValidationError) as cm:
            services.confirm_order(order.id)
        self.assertEqual(cm.exception.code, "ORDER_NOT_PRICED")

    def test_partially_priced_order_cannot_be_confirmed(self):
        order = self.quote([self.catalog_item(), self.custom_item()])
        with self.assertRaises(ValidationError):
            services.confirm_order(order.id)
        custom = order.items.get(is_custom=True)
        services.price_item(order.id, custom.id, unit_price=30000)
        self.assertEqual(services.confirm_order(order.id).subtotal, MOTIF + 30000)

    def test_confirm_is_idempotent_and_resends_email(self):
        order = self.priced_quote()
        with self.captureOnCommitCallbacks(execute=True):
            first = services.confirm_order(order.id)
            second = services.confirm_order(order.id)
        self.assertEqual(first.confirmed_at, second.confirmed_at)
        self.assertEqual(second.status, OrderStatus.NEW)
        self.assertEqual(len(mail.outbox), 2)

    def test_confirm_message_goes_into_email(self):
        order = self.priced_quote()
        with self.captureOnCommitCallbacks(execute=True):
            services.confirm_order(order.id, message="Návrh jsme ještě trochu upravili.")
        self.assertIn("Návrh jsme ještě trochu upravili.", mail.outbox[0].body)

    def test_changing_price_cancels_confirmation(self):
        order = services.confirm_order(self.priced_quote().id)
        self.assertIsNotNone(order.confirmed_at)
        changed = services.price_item(order.id, order.items.get().id, unit_price=40000)
        self.assertIsNone(changed.confirmed_at)
        self.assertEqual(changed.status, OrderStatus.NEW)               # zůstává NEW, čeká na nové potvrzení
        self.assertEqual(changed.total, 40000 + SHIPPING_BASE)
        reconfirmed = services.confirm_order(order.id)
        self.assertIsNotNone(reconfirmed.confirmed_at)

    def test_unchanged_values_keep_confirmation(self):
        order = services.confirm_order(self.priced_quote().id)
        same = services.price_item(order.id, order.items.get().id, unit_price=45000)
        self.assertEqual(same.confirmed_at, order.confirmed_at)

    def test_quantity_change_recalculates_shipping(self):
        order = self.priced_quote()                                      # 1 ks → 99 Kč
        item = order.items.get()
        three = services.price_item(order.id, item.id, quantity=3)
        self.assertEqual(three.shipping_cost, SHIPPING_3_PCS)
        self.assertEqual(three.subtotal, 3 * 45000)
        self.assertEqual(three.total, 3 * 45000 + SHIPPING_3_PCS)
        five = services.price_item(order.id, item.id, quantity=5)
        self.assertEqual(five.shipping_cost, 0)

    def test_manual_shipping_survives_quantity_change(self):
        order = self.priced_quote()
        services.set_shipping_cost(order.id, 7000)
        changed = services.price_item(order.id, order.items.get().id, quantity=3)
        self.assertEqual(changed.shipping_cost, 7000)
        self.assertTrue(changed.shipping_cost_manual)

    def test_set_manual_shipping_and_reset_to_automatic(self):
        order = services.confirm_order(self.priced_quote().id)
        manual = services.set_shipping_cost(order.id, 7000)
        self.assertEqual((manual.shipping_cost, manual.shipping_cost_manual), (7000, True))
        self.assertEqual(manual.total, 45000 + 7000)
        self.assertIsNone(manual.confirmed_at)                           # změna ceny ruší potvrzení
        auto = services.reset_shipping_cost(order.id)
        self.assertEqual((auto.shipping_cost, auto.shipping_cost_manual), (SHIPPING_BASE, False))

    def test_zero_shipping_is_free_not_unset(self):
        order = services.set_shipping_cost(self.priced_quote().id, 0)
        self.assertEqual(order.shipping_cost, 0)
        self.assertEqual(order.total, 45000)

    def test_null_price_returns_item_to_waiting(self):
        order = self.priced_quote()
        back = services.price_item(order.id, order.items.get().id, unit_price=None)
        self.assertIsNone(back.total)
        self.assertIsNone(back.subtotal)

    def test_validation_of_inputs(self):
        order = self.quote()
        item_id = order.items.get().id
        with self.assertRaises(ValidationError):
            services.price_item(order.id, item_id, unit_price=-1)
        for bad in (0, 501):
            with self.assertRaises(ValidationError):
                services.price_item(order.id, item_id, quantity=bad)
        with self.assertRaises(ValidationError):
            services.set_shipping_cost(order.id, -1)

    def test_item_of_another_order_rejected(self):
        order, other = self.quote(), self.quote()
        with self.assertRaises(ValidationError) as cm:
            services.price_item(order.id, other.items.get().id, unit_price=100)
        self.assertEqual(cm.exception.code, "ITEM_NOT_FOUND")

    def test_mixed_order_keeps_catalog_price(self):
        order = self.quote([self.catalog_item(), self.custom_item()])
        custom = order.items.get(is_custom=True)
        priced = services.price_item(order.id, custom.id, unit_price=30000)
        self.assertEqual(priced.subtotal, MOTIF + 30000)

    def test_locked_after_production_starts(self):
        order = services.confirm_order(self.priced_quote().id)
        services.start_processing(order.id, actor=self.staff)
        item_id = order.items.get().id
        for call in (
            lambda: services.price_item(order.id, item_id, unit_price=1),
            lambda: services.set_shipping_cost(order.id, 1),
            lambda: services.reset_shipping_cost(order.id),
        ):
            with self.assertRaises(OrderLocked) as cm:
                call()
            self.assertEqual(cm.exception.code, "ORDER_LOCKED")

    def test_cancelled_order_is_locked(self):
        order = self.priced_quote()
        services.cancel(order.id, actor=self.staff)
        with self.assertRaises(OrderLocked):
            services.price_item(order.id, order.items.get().id, unit_price=1)

    def test_cannot_confirm_after_production_starts(self):
        order = services.confirm_order(self.priced_quote().id)
        services.start_processing(order.id, actor=self.staff)
        with self.assertRaises(InvalidTransition):
            services.confirm_order(order.id)

    def test_catalog_order_can_be_reconfirmed_after_admin_change(self):
        order = self.make_order([self.catalog_item()])                  # automaticky potvrzená
        self.assertIsNotNone(order.confirmed_at)
        changed = services.set_shipping_cost(order.id, 0)               # admin dopravu daroval
        self.assertIsNone(changed.confirmed_at)
        with self.assertRaisesMessage(InvalidTransition, "není potvrzená"):
            services.start_processing(order.id, actor=self.staff)       # dokud znovu nepotvrdí


# -----------------------------------------------------------------------------
# STAVOVÝ AUTOMAT A ROLE
# -----------------------------------------------------------------------------

class TransitionTests(OrderTestBase):
    def new_order(self, payment_method):
        return self.make_order([self.catalog_item()], payment_method=payment_method)

    def test_online_full_flow(self):
        order = self.new_order(PaymentMethod.ONLINE)
        with self.assertRaisesMessage(InvalidTransition, "Online platba"):
            services.start_processing(order.id, actor=self.staff)       # nejdřív musí přijít platba od brány
        outcome = self.pay_online(order)
        self.assertTrue(outcome.order_marked_paid)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), (OrderStatus.PAID, PaymentStatus.PAID))
        services.start_processing(order.id, actor=self.staff)
        order = services.ship(order.id, actor=self.staff, tracking_number="Z123")
        self.assertEqual((order.tracking_number, order.shipment_status), ("Z123", ShipmentStatus.HANDED_OVER))
        order = services.mark_delivered(order.id, actor=self.staff)
        self.assertEqual((order.status, order.shipment_status), (OrderStatus.DELIVERED, ShipmentStatus.DELIVERED))

    def test_unconfirmed_order_cannot_start_production(self):
        order = self.admin_order([self.catalog_item()])                  # NEW, nepotvrzená
        self.assertEqual(order.status, OrderStatus.NEW)
        with self.assertRaisesMessage(InvalidTransition, "není potvrzená"):
            services.start_processing(order.id, actor=self.staff)
        services.confirm_order(order.id)
        self.assertEqual(services.start_processing(order.id, actor=self.staff).status, OrderStatus.PROCESSING)

    def test_cash_on_delivery_can_start_production_right_after_confirmation(self):
        order = services.start_processing(self.new_order(PaymentMethod.CASH_ON_DELIVERY).id, actor=self.staff)
        self.assertEqual((order.status, order.payment_status), (OrderStatus.PROCESSING, PaymentStatus.PENDING))

    def test_cash_on_delivery_paid_request_goes_to_processing(self):
        """Dobírka nikdy neprochází stavem PAID — požadavek na PAID se přesměruje na PROCESSING."""
        order = services.mark_paid(self.new_order(PaymentMethod.CASH_ON_DELIVERY).id, actor=self.staff)
        self.assertEqual((order.status, order.payment_status), (OrderStatus.PROCESSING, PaymentStatus.PENDING))

    def test_cash_on_delivery_is_paid_on_delivery(self):
        order = self.new_order(PaymentMethod.CASH_ON_DELIVERY)
        services.start_processing(order.id, actor=self.staff)
        services.ship(order.id, actor=self.staff)
        order = services.mark_delivered(order.id, actor=self.staff)
        self.assertEqual(order.payment_status, PaymentStatus.PAID)

    def test_online_paid_manually_only_by_admin(self):
        """Online platbu potvrzuje brána; ručně ji smí označit jen admin (např. při výpadku brány)."""
        order = self.new_order(PaymentMethod.ONLINE)
        with self.assertRaises(PermissionDenied):
            services.mark_paid(order.id, actor=self.staff)
        self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.NEW)
        paid = services.mark_paid(order.id, actor=self.admin)
        self.assertEqual((paid.status, paid.payment_status), (OrderStatus.PAID, PaymentStatus.PAID))

    def test_quote_cannot_jump_to_new_without_confirmation(self):
        order = self.make_order([self.custom_item()])
        with self.assertRaisesMessage(InvalidTransition, "potvrzení"):
            services.transition_order(order.id, OrderStatus.NEW, actor=self.admin)

    def test_skipping_states_rejected(self):
        order = self.new_order(PaymentMethod.CASH_ON_DELIVERY)
        with self.assertRaisesMessage(InvalidTransition, "není povolený"):
            services.transition_order(order.id, OrderStatus.SHIPPED, actor=self.admin)

    def test_cannot_ship_without_carrier(self):
        order = self.new_order(PaymentMethod.CASH_ON_DELIVERY)
        Order.objects.filter(pk=order.pk).update(carrier=None)
        services.start_processing(order.id, actor=self.staff)
        with self.assertRaisesMessage(InvalidTransition, "dopravce"):
            services.ship(order.id, actor=self.staff)

    def test_unpaid_order_can_be_cancelled(self):
        order = services.cancel(self.new_order(PaymentMethod.ONLINE).id, actor=self.staff)
        self.assertEqual(order.status, OrderStatus.CANCELLED)

    def test_quote_can_be_cancelled(self):
        order = self.make_order([self.custom_item()])
        self.assertEqual(services.cancel(order.id, actor=self.staff).status, OrderStatus.CANCELLED)

    def test_paid_order_cannot_be_cancelled_only_refunded(self):
        order = self.new_order(PaymentMethod.ONLINE)
        self.pay_online(order)
        with self.assertRaises(InvalidTransition):                       # ze stavu PAID zrušení neexistuje
            services.cancel(order.id, actor=self.admin)
        services.start_processing(order.id, actor=self.staff)
        with self.assertRaisesMessage(InvalidTransition, "REFUNDED"):    # ani za výroby, když je zaplaceno
            services.cancel(order.id, actor=self.admin)
        order = services.refund(order.id, actor=self.admin)
        self.assertEqual((order.status, order.payment_status), (OrderStatus.REFUNDED, PaymentStatus.REFUNDED))

    def test_refund_marks_gateway_payments_refunded(self):
        order = self.new_order(PaymentMethod.ONLINE)
        self.pay_online(order)
        services.refund(order.id, actor=self.admin)
        self.assertEqual(list(order.payments.values_list("status", flat=True)), [PaymentStatus.REFUNDED])

    def test_unpaid_order_cannot_be_refunded(self):
        order = self.new_order(PaymentMethod.ONLINE)
        with self.assertRaises(InvalidTransition):
            services.refund(order.id, actor=self.admin)

    def test_final_states_are_final(self):
        order = services.cancel(self.new_order(PaymentMethod.ONLINE).id, actor=self.staff)
        with self.assertRaises(InvalidTransition):
            services.transition_order(order.id, OrderStatus.NEW, actor=self.admin)

    # -- role --
    def test_staff_cannot_refund_but_admin_can(self):
        order = self.new_order(PaymentMethod.ONLINE)
        self.pay_online(order)
        with self.assertRaises(PermissionDenied):
            services.refund(order.id, actor=self.staff)
        self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.PAID)   # nic se nezměnilo
        self.assertEqual(services.refund(order.id, actor=self.admin).status, OrderStatus.REFUNDED)

    def test_staff_cannot_cancel_order_in_production_but_admin_can(self):
        order = services.start_processing(self.new_order(PaymentMethod.CASH_ON_DELIVERY).id, actor=self.staff)
        with self.assertRaises(PermissionDenied):
            services.cancel(order.id, actor=self.staff)
        self.assertEqual(services.cancel(order.id, actor=self.admin).status, OrderStatus.CANCELLED)

    def test_staff_can_do_everyday_work(self):
        order = self.new_order(PaymentMethod.ONLINE)
        services.cancel(self.new_order(PaymentMethod.ONLINE).id, actor=self.staff)   # NEW → CANCELLED
        self.pay_online(order)                                           # zaplatí brána, ne staff
        services.start_processing(order.id, actor=self.staff)
        services.ship(order.id, actor=self.staff)
        self.assertEqual(services.mark_delivered(order.id, actor=self.staff).status, OrderStatus.DELIVERED)


# -----------------------------------------------------------------------------
# ONLINE PLATBA PŘES BRÁNU
# -----------------------------------------------------------------------------

class OnlinePaymentTests(OrderTestBase):
    RETURN = "https://trikonos.cz/dekujeme"

    def online_order(self, items=None, **kw):
        return self.make_order(items or [self.catalog_item()], payment_method=PaymentMethod.ONLINE, **kw)

    def start(self, order):
        return services.start_online_payment(order.id, return_url=self.RETURN)

    def gateway_says(self, payment, status):
        DummyGateway.statuses[payment.provider_payment_id] = status

    def notify(self, payment):
        return services.handle_payment_notification(payment.provider, payment.provider_payment_id)

    # -- založení platby --
    def test_start_creates_pending_payment_with_server_side_amount(self):
        order = self.online_order()
        payment = self.start(order)
        self.assertEqual((payment.status, payment.amount, payment.provider), (PaymentStatus.PENDING, order.total, "dummy"))
        self.assertTrue(payment.redirect_url.startswith("https://pay.example/"))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.PENDING)

    def test_cash_on_delivery_order_is_not_paid_online(self):
        order = self.make_order([self.catalog_item()], payment_method=PaymentMethod.CASH_ON_DELIVERY)
        with self.assertRaisesMessage(ValidationError, "neplatí online"):
            self.start(order)

    def test_quote_cannot_be_paid_before_confirmation(self):
        order = self.online_order([self.custom_item()])
        with self.assertRaises(InvalidTransition):
            self.start(order)

    def test_unconfirmed_order_cannot_be_paid(self):
        order = self.admin_order([self.catalog_item()], payment_method=PaymentMethod.ONLINE)   # NEW, nepotvrzená
        with self.assertRaises(ValidationError) as cm:
            self.start(order)
        self.assertEqual(cm.exception.code, "ORDER_NOT_CONFIRMED")

    def test_paid_order_cannot_be_paid_again(self):
        order = self.online_order()
        self.pay_online(order)
        with self.assertRaises(InvalidTransition):
            self.start(order)

    def test_new_attempt_replaces_the_pending_one(self):
        order = self.online_order()
        first, second = self.start(order), self.start(order)
        first.refresh_from_db()
        self.assertEqual(first.status, PaymentStatus.FAILED)
        self.assertEqual(list(order.payments.filter(status=PaymentStatus.PENDING)), [second])

    def test_unconfigured_gateway_is_a_configuration_error(self):
        order = self.online_order()
        with override_settings(TRIKONOS_PAYMENT_GATEWAY=None):
            with self.assertRaises(ImproperlyConfigured):
                self.start(order)

    def test_provider_payment_id_is_unique(self):
        order = self.online_order()
        payment = self.start(order)
        with self.assertRaises(IntegrityError):
            Payment.objects.create(order=order, provider=payment.provider,
                                   provider_payment_id=payment.provider_payment_id, amount=1)

    # -- notifikace od brány --
    def test_successful_notification_marks_order_paid(self):
        order = self.online_order()
        payment = self.start(order)
        self.gateway_says(payment, PaymentStatus.PAID)
        with self.captureOnCommitCallbacks(execute=True):
            outcome = self.notify(payment)
        self.assertEqual((outcome.order_marked_paid, outcome.needs_attention), (True, False))
        payment.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PAID)
        self.assertIsNotNone(payment.paid_at)
        self.assertTrue(payment.raw_payload["dummy"])                    # odpověď brány se uloží
        self.assertEqual((order.status, order.payment_status), (OrderStatus.PAID, PaymentStatus.PAID))
        self.assertIn("přijata", mail.outbox[-1].subject)

    def test_notification_is_idempotent(self):
        order = self.online_order()
        payment = self.start(order)
        self.gateway_says(payment, PaymentStatus.PAID)
        with self.captureOnCommitCallbacks(execute=True):
            self.notify(payment)
            again = self.notify(payment)
        self.assertEqual((again.order_marked_paid, again.needs_attention), (False, False))
        self.assertEqual(len([m for m in mail.outbox if "přijata" in m.subject and "Platba" in m.subject]), 1)

    def test_status_is_taken_from_gateway_not_from_the_caller(self):
        """Notifikace nenese stav — i podvržená nemůže objednávku označit jako zaplacenou."""
        order = self.online_order()
        payment = self.start(order)                                      # brána stále říká PENDING
        outcome = self.notify(payment)
        payment.refresh_from_db()
        order.refresh_from_db()
        self.assertFalse(outcome.order_marked_paid)
        self.assertEqual((payment.status, order.status), (PaymentStatus.PENDING, OrderStatus.NEW))

    def test_failed_payment_leaves_order_open_and_allows_retry(self):
        order = self.online_order()
        payment = self.start(order)
        self.gateway_says(payment, PaymentStatus.FAILED)
        self.notify(payment)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), (OrderStatus.NEW, PaymentStatus.FAILED))
        outcome = self.pay_online(order)                                 # další pokus
        self.assertTrue(outcome.order_marked_paid)
        self.assertEqual(order.payments.count(), 2)

    def test_unknown_payment_and_wrong_provider_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            services.handle_payment_notification("dummy", "NEEXISTUJE")
        self.assertEqual(cm.exception.code, "PAYMENT_NOT_FOUND")
        payment = self.start(self.online_order())
        with self.assertRaises(ValidationError):
            services.handle_payment_notification("gopay", payment.provider_payment_id)

    # -- hraniční případy: peníze přišly, ale objednávku nejde označit jako zaplacenou --
    def test_price_change_invalidates_pending_payment_and_confirmation(self):
        order = self.online_order()
        payment = self.start(order)
        services.set_shipping_cost(order.id, 1000)
        payment.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.FAILED)           # stará částka už nejde zaplatit
        self.assertIsNone(order.confirmed_at)
        with self.assertRaises(ValidationError) as cm:
            self.start(order)
        self.assertEqual(cm.exception.code, "ORDER_NOT_CONFIRMED")
        services.confirm_order(order.id)
        self.assertEqual(self.start(order).amount, order.items.get().unit_price + 1000)   # nová částka

    def test_payment_of_old_amount_after_price_change_needs_attention(self):
        order = self.online_order()
        payment = self.start(order)
        services.price_item(order.id, order.items.get().id, unit_price=40000)    # cena se změnila
        self.gateway_says(payment, PaymentStatus.PAID)                   # zákazník ale zaplatil starou částku
        with self.assertLogs(services.logger, level="WARNING"):          # člověk se o tom dozví i z logu
            outcome = self.notify(payment)
        order.refresh_from_db()
        self.assertEqual((outcome.order_marked_paid, outcome.needs_attention), (False, True))
        self.assertIn("neodpovídá", outcome.reason)
        self.assertEqual(order.status, OrderStatus.NEW)
        self.assertNotEqual(order.payment_status, PaymentStatus.PAID)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PAID)             # peníze přišly, to se zaznamená

    def test_payment_for_cancelled_order_needs_attention(self):
        order = self.online_order()
        payment = self.start(order)
        services.cancel(order.id, actor=self.staff)
        self.gateway_says(payment, PaymentStatus.PAID)
        with self.assertLogs(services.logger, level="WARNING"):
            outcome = self.notify(payment)
        order.refresh_from_db()
        self.assertTrue(outcome.needs_attention)
        self.assertIn("zrušené", outcome.reason)
        self.assertEqual(order.status, OrderStatus.CANCELLED)

    def test_duplicate_payment_needs_attention(self):
        order = self.online_order()
        services.mark_paid(order.id, actor=self.admin)                   # admin ji ručně označil jako zaplacenou
        extra = Payment.objects.create(order=order, provider="dummy", provider_payment_id="DUMMY-X", amount=order.total)
        DummyGateway.statuses["DUMMY-X"] = PaymentStatus.PAID
        with self.assertLogs(services.logger, level="WARNING"):
            outcome = self.notify(extra)
        self.assertTrue(outcome.needs_attention)
        self.assertIn("duplicitní", outcome.reason)

    @override_settings(TRIKONOS_ADMIN_EMAIL="obchod@trikonos.cz")
    def test_shop_is_notified_when_payment_needs_attention(self):
        order = self.online_order()
        payment = self.start(order)
        services.cancel(order.id, actor=self.staff)
        self.gateway_says(payment, PaymentStatus.PAID)
        with self.assertLogs(services.logger, level="WARNING"), self.captureOnCommitCallbacks(execute=True):
            self.notify(payment)
        shop_mail = [m for m in mail.outbox if m.to == ["obchod@trikonos.cz"]]
        self.assertEqual(len(shop_mail), 1)
        self.assertIn("vyžaduje pozornost", shop_mail[0].subject)

    # -- poptávka: ocenění → potvrzení → platba → výroba --
    def test_quote_flow_end_to_end(self):
        order = self.online_order([self.custom_item()])                  # poptávka bez ceny
        self.assertEqual(order.status, OrderStatus.QUOTE_REQUESTED)
        services.price_item(order.id, order.items.get().id, unit_price=45000)
        with self.captureOnCommitCallbacks(execute=True):
            confirmed = services.confirm_order(order.id)
        self.assertIn("https://trikonos.cz/objednavka/", mail.outbox[-1].body)    # odkaz, kde zákazník zaplatí
        self.assertEqual(self.start(confirmed).amount, 45000 + SHIPPING_BASE)
        payment = order.payments.get()
        self.gateway_says(payment, PaymentStatus.PAID)
        self.assertTrue(self.notify(payment).order_marked_paid)
        self.assertEqual(services.start_processing(order.id, actor=self.staff).status, OrderStatus.PROCESSING)


# -----------------------------------------------------------------------------
# PŘÍSTUPOVÉ TOKENY K OBJEDNÁVCE
# -----------------------------------------------------------------------------

class OrderAccessTokenTests(TestCase):
    def test_roundtrip(self):
        order_id = uuid.uuid4()
        self.assertEqual(order_access.resolve_access_token(order_access.make_access_token(order_id)), order_id)

    def test_tampered_and_garbage_tokens_rejected(self):
        token = order_access.make_access_token(uuid.uuid4())
        for bad in (token[:-2] + "xx", "nesmysl", ""):
            with self.assertRaises(ValidationError) as cm:
                order_access.resolve_access_token(bad)
            self.assertEqual(cm.exception.code, "INVALID_ACCESS_TOKEN")

    def test_expired_token_rejected(self):
        token = order_access.make_access_token(uuid.uuid4())
        with mock.patch.object(order_access, "MAX_AGE", -1):
            with self.assertRaises(ValidationError):
                order_access.resolve_access_token(token)

    def test_token_is_valid_for_180_days(self):
        self.assertEqual(order_access.MAX_AGE, 180 * 24 * 3600)


# -----------------------------------------------------------------------------
# STAV PLATBY A ZÁSILKY (nezávisle na stavu objednávky)
# -----------------------------------------------------------------------------

class PaymentStatusTests(OrderTestBase):
    def cod_order(self):
        return self.make_order([self.catalog_item()], payment_method=PaymentMethod.CASH_ON_DELIVERY)

    def online_order(self):
        return self.make_order([self.catalog_item()], payment_method=PaymentMethod.ONLINE)

    def test_cash_collected_can_be_confirmed_by_staff_without_moving_order_status(self):
        order = services.set_payment_status(self.cod_order().id, PaymentStatus.PAID, actor=self.staff)
        self.assertEqual((order.payment_status, order.status), (PaymentStatus.PAID, OrderStatus.NEW))

    def test_online_paid_manually_only_by_admin(self):
        order = self.online_order()
        with self.assertRaises(PermissionDenied):
            services.set_payment_status(order.id, PaymentStatus.PAID, actor=self.staff)
        self.assertEqual(services.set_payment_status(order.id, PaymentStatus.PAID, actor=self.admin).payment_status, PaymentStatus.PAID)

    def test_refunded_only_by_admin(self):
        order = self.cod_order()
        services.set_payment_status(order.id, PaymentStatus.PAID, actor=self.staff)
        with self.assertRaises(PermissionDenied):
            services.set_payment_status(order.id, PaymentStatus.REFUNDED, actor=self.staff)
        self.assertEqual(services.set_payment_status(order.id, PaymentStatus.REFUNDED, actor=self.admin).payment_status, PaymentStatus.REFUNDED)

    def test_meaningful_transitions_only(self):
        order = self.cod_order()
        with self.assertRaises(InvalidTransition):
            services.set_payment_status(order.id, PaymentStatus.REFUNDED, actor=self.admin)   # nikdy nezaplaceno
        services.set_payment_status(order.id, PaymentStatus.PAID, actor=self.staff)
        with self.assertRaises(InvalidTransition):
            services.set_payment_status(order.id, PaymentStatus.PENDING, actor=self.admin)    # zaplaceno se nevrací

    def test_failed_payment_can_be_retried(self):
        order = self.cod_order()
        services.set_payment_status(order.id, PaymentStatus.FAILED, actor=self.staff)
        self.assertEqual(services.set_payment_status(order.id, PaymentStatus.PAID, actor=self.staff).payment_status, PaymentStatus.PAID)

    def test_cancelled_order_payment_is_frozen(self):
        order = self.cod_order()
        services.cancel(order.id, actor=self.staff)
        with self.assertRaises(InvalidTransition):
            services.set_payment_status(order.id, PaymentStatus.PAID, actor=self.staff)

    def test_order_without_price_cannot_be_marked_paid(self):
        quote = self.make_order([self.custom_item()], payment_method=PaymentMethod.CASH_ON_DELIVERY)
        with self.assertRaisesMessage(InvalidTransition, "bez ceny"):
            services.set_payment_status(quote.id, PaymentStatus.PAID, actor=self.staff)

    def test_same_status_is_noop_and_garbage_rejected(self):
        order = self.cod_order()
        self.assertEqual(services.set_payment_status(order.id, PaymentStatus.PENDING, actor=self.staff).payment_status, PaymentStatus.PENDING)
        with self.assertRaises(ValidationError):
            services.set_payment_status(order.id, "NESMYSL", actor=self.staff)


class ShipmentStatusTests(OrderTestBase):
    def producing_order(self):
        order = self.make_order([self.catalog_item()], payment_method=PaymentMethod.CASH_ON_DELIVERY)
        return services.start_processing(order.id, actor=self.staff)

    def test_moves_forward_only(self):
        order = self.producing_order()
        services.set_shipment_status(order.id, ShipmentStatus.LABEL_PRINTED)
        services.set_shipment_status(order.id, ShipmentStatus.HANDED_OVER, tracking_number="Z1")
        with self.assertRaises(InvalidTransition):
            services.set_shipment_status(order.id, ShipmentStatus.CREATED)
        result = services.set_shipment_status(order.id, ShipmentStatus.IN_TRANSIT)
        self.assertEqual((result.shipment_status, result.tracking_number), (ShipmentStatus.IN_TRANSIT, "Z1"))

    def test_return_only_after_handover(self):
        order = self.producing_order()
        with self.assertRaises(InvalidTransition):
            services.set_shipment_status(order.id, ShipmentStatus.RETURNED)
        services.set_shipment_status(order.id, ShipmentStatus.HANDED_OVER)
        self.assertEqual(services.set_shipment_status(order.id, ShipmentStatus.RETURNED).shipment_status, ShipmentStatus.RETURNED)

    def test_status_not_changeable_before_production(self):
        order = self.make_order([self.catalog_item()])
        with self.assertRaises(InvalidTransition):
            services.set_shipment_status(order.id, ShipmentStatus.LABEL_PRINTED)

    def test_tracking_number_can_be_corrected_anytime(self):
        order = self.make_order([self.catalog_item()])                  # ještě NEW
        self.assertEqual(services.set_shipment_status(order.id, tracking_number=" Z999 ").tracking_number, "Z999")

    def test_garbage_rejected(self):
        order = self.producing_order()
        with self.assertRaises(ValidationError):
            services.set_shipment_status(order.id, "NESMYSL")


# -----------------------------------------------------------------------------
# ZÁKAZNÍCI
# -----------------------------------------------------------------------------

class CustomerTests(OrderTestBase):
    def test_same_email_reuses_customer_case_insensitive(self):
        services.upsert_customer(self.contact("Jan@Example.com"))
        services.upsert_customer(self.contact("jan@example.COM"))
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Customer.objects.get().email, "jan@example.com")

    def test_new_data_fills_in_but_never_erases(self):
        services.upsert_customer(self.contact(phone="123", first_name="Jan"))
        customer = services.upsert_customer(self.contact(last_name="Novák"))   # bez telefonu
        self.assertEqual((customer.phone, customer.first_name, customer.last_name), ("123", "Jan", "Novák"))

    def test_marketing_consent_never_revoked_by_unchecked_box(self):
        services.upsert_customer(self.contact(marketing_consent=True))
        customer = services.upsert_customer(self.contact(marketing_consent=False))
        self.assertTrue(customer.marketing_consent)

    def test_orders_link_to_one_customer(self):
        a = self.make_order([self.catalog_item()])
        b = self.make_order([self.catalog_item()])
        self.assertEqual(a.customer_id, b.customer_id)
        self.assertEqual(Customer.objects.get().orders.count(), 2)

    def test_email_is_required(self):
        with self.assertRaises(ValidationError):
            services.upsert_customer(self.contact("   "))


# -----------------------------------------------------------------------------
# E-MAILY
# -----------------------------------------------------------------------------

@override_settings(TRIKONOS_ADMIN_EMAIL="obchod@trikonos.cz")
class NotificationTests(OrderTestBase):
    def token_from(self, body):
        """Vytáhne přístupový token z odkazu na stránku objednávky v těle e-mailu."""
        return body.split("https://trikonos.cz/objednavka/")[1].split()[0]

    def test_online_catalog_order_gives_customer_price_and_pay_link_and_notifies_shop(self):
        with self.captureOnCommitCallbacks(execute=True):
            order = self.make_order([self.catalog_item()], payment_method=PaymentMethod.ONLINE)
        self.assertEqual([m.to[0] for m in mail.outbox], ["jan@example.com", "obchod@trikonos.cz"])
        body = mail.outbox[0].body
        self.assertIn(order.order_number, body)
        self.assertIn("648,00 Kč", body)                                # 549 + 99
        self.assertEqual(order_access.resolve_access_token(self.token_from(body)), order.id)   # odkaz vede na tuhle objednávku

    def test_cash_on_delivery_order_has_no_pay_link(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.make_order([self.catalog_item()], payment_method=PaymentMethod.CASH_ON_DELIVERY)
        self.assertIn("dobírku", mail.outbox[0].body)
        self.assertNotIn("objednavka/", mail.outbox[0].body)

    def test_quote_request_acknowledged_and_shop_notified(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.make_order([self.custom_item()])
        customer_mail, shop_mail = mail.outbox
        self.assertIn("Poptávka", customer_mail.subject)
        self.assertNotIn("Celkem", customer_mail.body)                  # cena ještě není
        self.assertIn("ocenění", shop_mail.body)

    def test_unconfirmed_order_promises_confirmation(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.admin_order([self.catalog_item()])
        self.assertIn("potvrdíme", mail.outbox[0].body)
        self.assertNotIn("Celkem", mail.outbox[0].body)

    def test_confirmation_email_has_total_message_and_pay_link(self):
        order = self.make_order([self.custom_item()], payment_method=PaymentMethod.ONLINE)
        services.price_item(order.id, order.items.get().id, unit_price=45000)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            services.confirm_order(order.id, message="Návrh jsme ještě trochu upravili.")
        body = mail.outbox[0].body
        self.assertIn("549,00 Kč", body)                                # 450 + 99
        self.assertIn("Návrh jsme ještě trochu upravili.", body)
        self.assertIn("https://trikonos.cz/objednavka/", body)

    def test_reconfirmation_after_price_change_sends_new_email(self):
        order = self.make_order([self.catalog_item()])
        services.price_item(order.id, order.items.get().id, unit_price=40000)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            services.confirm_order(order.id)
        self.assertIn("499,00 Kč", mail.outbox[0].body)                 # 400 + 99

    def test_payment_received_email(self):
        order = self.make_order([self.catalog_item()])
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.pay_online(order)
        received = [m for m in mail.outbox if "přijata" in m.subject]
        self.assertEqual(len(received), 1)
        self.assertIn("648,00 Kč", received[0].body)
        self.assertEqual(received[0].to, ["jan@example.com"])

    def test_shipping_email_contains_tracking_number(self):
        order = self.make_order([self.catalog_item()], payment_method=PaymentMethod.CASH_ON_DELIVERY)
        services.start_processing(order.id, actor=self.staff)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            services.ship(order.id, actor=self.staff, tracking_number="Z999")
        self.assertIn("Z999", mail.outbox[0].body)

    def test_cancellation_email(self):
        order = self.make_order([self.catalog_item()])
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            services.cancel(order.id, actor=self.staff)
        self.assertIn("zrušena", mail.outbox[0].subject)

    def test_no_email_when_order_creation_fails(self):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(ValidationError):
                self.make_order([])
        self.assertEqual(len(mail.outbox), 0)

    def test_shop_not_notified_when_admin_email_unset(self):
        with override_settings(TRIKONOS_ADMIN_EMAIL=None):
            with self.captureOnCommitCallbacks(execute=True):
                self.make_order([self.catalog_item()])
        self.assertEqual(len(mail.outbox), 1)

    def test_without_url_template_email_still_works(self):
        with override_settings(TRIKONOS_ORDER_URL_TEMPLATE=""):
            with self.captureOnCommitCallbacks(execute=True):
                self.make_order([self.catalog_item()], payment_method=PaymentMethod.ONLINE)
        self.assertIn("stránce své objednávky", mail.outbox[0].body)


# -----------------------------------------------------------------------------
# PODKLADY (TOKENY) A UŽIVATELÉ
# -----------------------------------------------------------------------------

class DesignFileTokenTests(TestCase):
    def test_roundtrip(self):
        token = design_files.make_file_token("https://cdn.example/a.png")
        self.assertEqual(design_files.resolve_file_token(token), "https://cdn.example/a.png")

    def test_tampered_token_rejected(self):
        token = design_files.make_file_token("https://cdn.example/a.png")
        with self.assertRaises(ValidationError) as cm:
            design_files.resolve_file_token(token[:-2] + "xx")
        self.assertEqual(cm.exception.code, "INVALID_FILE_TOKEN")

    def test_expired_token_rejected(self):
        token = design_files.make_file_token("https://cdn.example/a.png")
        with mock.patch.object(design_files, "MAX_AGE", -1):
            with self.assertRaises(ValidationError):
                design_files.resolve_file_token(token)

    def test_token_is_valid_for_seven_days(self):
        self.assertEqual(design_files.MAX_AGE, 7 * 24 * 3600)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])   # rychlé hashování jen v testech
class UserManagerTests(TestCase):
    """Regrese: bez vlastního manageru padal create_user i `manage.py createsuperuser`."""

    def test_create_user_with_email(self):
        user = User.objects.create_user(email="Jan@Trikonos.cz", password="tajne", name="Jan")
        self.assertEqual(user.email, "Jan@trikonos.cz")                 # doména malými písmeny
        self.assertTrue(user.check_password("tajne"))
        self.assertEqual(user.role, StaffRole.STAFF)
        self.assertFalse(user.is_staff)

    def test_create_superuser_is_admin(self):
        user = User.objects.create_superuser(email="root@trikonos.cz", password="tajne", name="Root")
        self.assertEqual(user.role, StaffRole.ADMIN)
        self.assertTrue(user.is_staff and user.is_superuser)

    def test_email_is_required(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="x", name="X")
