import pytest
from rest_framework.test import APIClient

from accounts.models import User
from orders.models import DeliveryType

from .models import Carrier, CodSurcharge, PacketaConfig, ShippingPrice, ShippingQuantityDiscount

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client():
    user = User.objects.create_user(email='admin@trikonos.cz', password='x', name='Admin')
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def shipping_price(carrier):
    return ShippingPrice.objects.create(carrier=carrier, delivery_type=DeliveryType.HOME_DELIVERY, price=5000)


# ---------------------------------------------------------------------------
# Carriers
# ---------------------------------------------------------------------------

def test_can_list_carriers(admin_client, carrier):
    response = admin_client.get('/api/v1/admin/carriers')
    assert response.status_code == 200
    assert len(response.data['results']) == 1


def test_unauthenticated_cannot_list_carriers():
    response = APIClient().get('/api/v1/admin/carriers')
    assert response.status_code in (401, 403)


def test_admin_create_carrier_uppercases_code(admin_client):
    response = admin_client.post('/api/v1/admin/carriers', {'name': 'DPD', 'code': 'dpd'}, format='json')

    assert response.status_code == 201
    assert response.data['code'] == 'DPD'


def test_admin_can_reorder_carriers(admin_client, carrier):
    second = Carrier.objects.create(code='DPD', name='DPD')

    response = admin_client.post(
        '/api/v1/admin/carriers/reorder', {'ids': [str(second.id), str(carrier.id)]}, format='json',
    )

    assert response.status_code == 204
    second.refresh_from_db()
    carrier.refresh_from_db()
    assert second.position < carrier.position


def test_admin_can_deactivate_carrier_instead_of_deleting(admin_client, carrier):
    response = admin_client.patch(f'/api/v1/admin/carriers/{carrier.id}', {'isActive': False}, format='json')

    assert response.status_code == 200
    carrier.refresh_from_db()
    assert carrier.is_active is False


# ---------------------------------------------------------------------------
# Shipping prices
# ---------------------------------------------------------------------------

def test_create_shipping_price(admin_client, carrier):
    response = admin_client.post(
        '/api/v1/admin/shipping-prices',
        {'carrierId': str(carrier.id), 'deliveryType': DeliveryType.PICKUP_POINT, 'price': 7900},
        format='json',
    )

    assert response.status_code == 201
    assert ShippingPrice.objects.filter(carrier=carrier, delivery_type=DeliveryType.PICKUP_POINT, price=7900).exists()


def test_duplicate_shipping_price_for_same_pair_returns_400(admin_client, shipping_price, carrier):
    response = admin_client.post(
        '/api/v1/admin/shipping-prices',
        {'carrierId': str(carrier.id), 'deliveryType': DeliveryType.HOME_DELIVERY, 'price': 1000},
        format='json',
    )

    # Documented as 400 (not 409) for this one - see AdminShippingPriceViewSet's
    # extra_note; unlike the quantity-discount tiers, this is deliberate.
    assert response.status_code == 400


def test_can_read_and_write_shipping_prices(admin_client, shipping_price):
    read = admin_client.get('/api/v1/admin/shipping-prices')
    assert read.status_code == 200

    write = admin_client.patch(f'/api/v1/admin/shipping-prices/{shipping_price.id}', {'price': 1000}, format='json')
    assert write.status_code == 200


# ---------------------------------------------------------------------------
# COD surcharge (singleton)
# ---------------------------------------------------------------------------

def test_can_view_cod_surcharge(admin_client):
    response = admin_client.get('/api/v1/admin/cod-surcharge')
    assert response.status_code == 200
    assert response.data['amount'] == 0


def test_admin_can_update_cod_surcharge(admin_client):
    response = admin_client.put('/api/v1/admin/cod-surcharge', {'amount': 2900}, format='json')

    assert response.status_code == 200
    assert CodSurcharge.get_solo().amount == 2900


# ---------------------------------------------------------------------------
# Packeta config (singleton, requires authentication for both read and write)
# ---------------------------------------------------------------------------

def test_unauthenticated_cannot_view_packeta_config():
    response = APIClient().get('/api/v1/admin/packeta-config')
    assert response.status_code in (401, 403)


def test_admin_can_set_packeta_config(admin_client):
    response = admin_client.put(
        '/api/v1/admin/packeta-config', {'apiPassword': 'secret', 'eshop': 'trikonos'}, format='json',
    )

    assert response.status_code == 200
    config = PacketaConfig.get_solo()
    assert config.api_password == 'secret'
    assert config.eshop == 'trikonos'


def test_duplicate_min_quantity_on_create_returns_409(admin_client, shipping_price):
    ShippingQuantityDiscount.objects.create(shipping_price=shipping_price, min_quantity=30, discount_percent=30)

    response = admin_client.post(
        '/api/v1/admin/shipping-quantity-discounts',
        {'shippingPriceId': str(shipping_price.id), 'minQuantity': 30, 'discountPercent': 50},
        format='json',
    )

    assert response.status_code == 409
    assert response.data['error']['code'] == 'DUPLICATE_QUANTITY_DISCOUNT'
    assert ShippingQuantityDiscount.objects.filter(shipping_price=shipping_price).count() == 1


def test_duplicate_min_quantity_on_update_returns_409(admin_client, shipping_price):
    ShippingQuantityDiscount.objects.create(shipping_price=shipping_price, min_quantity=30, discount_percent=30)
    other = ShippingQuantityDiscount.objects.create(shipping_price=shipping_price, min_quantity=50, discount_percent=100)

    response = admin_client.patch(
        f'/api/v1/admin/shipping-quantity-discounts/{other.id}', {'minQuantity': 30}, format='json',
    )

    assert response.status_code == 409
    assert response.data['error']['code'] == 'DUPLICATE_QUANTITY_DISCOUNT'
    other.refresh_from_db()
    assert other.min_quantity == 50


def test_updating_a_tier_to_its_own_min_quantity_is_not_a_false_conflict(admin_client, shipping_price):
    tier = ShippingQuantityDiscount.objects.create(shipping_price=shipping_price, min_quantity=30, discount_percent=30)

    response = admin_client.patch(
        f'/api/v1/admin/shipping-quantity-discounts/{tier.id}', {'minQuantity': 30, 'discountPercent': 40}, format='json',
    )

    assert response.status_code == 200
    tier.refresh_from_db()
    assert tier.discount_percent == 40
