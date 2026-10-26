import pytest
from rest_framework.test import APIClient

from orders.models import DeliveryType
from shipping.models import ShippingPrice

pytestmark = pytest.mark.django_db


def _cart_body(motif, product_type, cut, color, **extra):
    body = {
        'items': [{
            'isCustom': False, 'motifId': str(motif.id), 'productTypeId': str(product_type.id),
            'cutId': str(cut.id), 'colorId': str(color.id), 'size': 'M', 'quantity': 1,
        }],
    }
    body.update(extra)
    return body


def test_cart_validate_without_delivery_choice_returns_null_shipping_cost(motif, product_type, cut, color):
    client = APIClient()

    response = client.post('/api/v1/cart/validate', _cart_body(motif, product_type, cut, color), format='json')

    assert response.status_code == 200
    assert response.data['shipping_cost'] is None
    assert response.data['total'] is None


def test_cart_validate_with_configured_delivery_returns_shipping_cost(motif, product_type, cut, color, carrier):
    ShippingPrice.objects.create(carrier=carrier, delivery_type=DeliveryType.HOME_DELIVERY, price=5000)
    client = APIClient()
    body = _cart_body(
        motif, product_type, cut, color,
        carrierId=str(carrier.id), deliveryType=DeliveryType.HOME_DELIVERY,
    )

    response = client.post('/api/v1/cart/validate', body, format='json')

    assert response.status_code == 200
    assert response.data['shipping_cost'] == 5000
    assert response.data['total'] == motif.price + 5000


def test_cart_validate_personal_pickup_is_free_without_carrier(motif, product_type, cut, color):
    client = APIClient()
    body = _cart_body(motif, product_type, cut, color, deliveryType=DeliveryType.PERSONAL_PICKUP)

    response = client.post('/api/v1/cart/validate', body, format='json')

    assert response.status_code == 200
    assert response.data['shipping_cost'] == 0
    assert response.data['total'] == motif.price
