import pytest
from rest_framework.test import APIClient

from accounts.models import StaffRole, User
from orders.models import DeliveryType

from .models import ShippingPrice, ShippingQuantityDiscount

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client():
    user = User.objects.create_user(email='admin@trikonos.cz', password='x', name='Admin', role=StaffRole.ADMIN)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def shipping_price(carrier):
    return ShippingPrice.objects.create(carrier=carrier, delivery_type=DeliveryType.HOME_DELIVERY, price=5000)


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
