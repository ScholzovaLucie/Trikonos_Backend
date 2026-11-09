"""POST /orders/lookup, GET /orders/access/{accessToken} and the fileToken
handed out by POST /uploads/designs - the only public ways back into an
order or a design upload, so a wrong guess must fail exactly like "doesn't
exist" (no information leak), and a tampered or outdated token must never
be honoured.
"""
import pytest
from rest_framework.test import APIClient

from core.tokens import sign_file_token, sign_order_access_token
from orders import services
from orders.models import DeliveryType, PaymentMethod

pytestmark = pytest.mark.django_db


def _order(motif, product_type, cut, color, email='jana@example.cz'):
    return services.create_order(
        customer_data={
            'email': email, 'phone': '+420777123456', 'first_name': 'Jana', 'last_name': 'Nováková',
            'company_name': '', 'marketing_consent': True,
        },
        billing_data={
            'billing_full_name': 'Jana Nováková', 'billing_company_name': '', 'billing_ico': '', 'billing_dic': '',
            'billing_street': 'Horní 12', 'billing_city': 'Vrchlabí', 'billing_zip': '54301', 'billing_country': 'CZ',
        },
        shipping_data={
            'shipping_full_name': 'Jana Nováková', 'shipping_company_name': '', 'shipping_street': 'Horní 12',
            'shipping_city': 'Vrchlabí', 'shipping_zip': '54301', 'shipping_country': 'CZ',
        },
        delivery_data={'carrier': None, 'delivery_type': None, 'pickup_point_id': '', 'pickup_point_name': ''},
        payment_method=PaymentMethod.BANK_TRANSFER,
        note='',
        items_data=[{
            'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
            'cut': cut, 'color': color, 'size': 'M', 'quantity': 1, 'placements': [],
        }],
    )


# ---------------------------------------------------------------------------
# POST /orders/lookup
# ---------------------------------------------------------------------------

def test_lookup_with_correct_number_and_email_returns_order(motif, product_type, cut, color):
    order = _order(motif, product_type, cut, color)
    client = APIClient()

    response = client.post(
        '/api/v1/orders/lookup', {'orderNumber': order.order_number, 'email': 'jana@example.cz'}, format='json',
    )

    assert response.status_code == 200
    assert response.data['order_number'] == order.order_number


def test_lookup_with_wrong_email_returns_404(motif, product_type, cut, color):
    order = _order(motif, product_type, cut, color)
    client = APIClient()

    response = client.post(
        '/api/v1/orders/lookup', {'orderNumber': order.order_number, 'email': 'not-jana@example.cz'}, format='json',
    )

    assert response.status_code == 404


def test_lookup_with_wrong_order_number_returns_the_same_404_as_wrong_email(motif, product_type, cut, color):
    order = _order(motif, product_type, cut, color)
    client = APIClient()

    wrong_email = client.post(
        '/api/v1/orders/lookup', {'orderNumber': order.order_number, 'email': 'not-jana@example.cz'}, format='json',
    )
    wrong_number = client.post(
        '/api/v1/orders/lookup', {'orderNumber': 'TRK-2026-9999', 'email': 'jana@example.cz'}, format='json',
    )

    assert wrong_email.status_code == wrong_number.status_code == 404
    assert wrong_email.data == wrong_number.data


# ---------------------------------------------------------------------------
# GET /orders/access/{accessToken}
# ---------------------------------------------------------------------------

def test_access_with_valid_token_returns_order(motif, product_type, cut, color):
    order = _order(motif, product_type, cut, color)
    token = sign_order_access_token(order.id)
    client = APIClient()

    response = client.get(f'/api/v1/orders/access/{token}')

    assert response.status_code == 200
    assert response.data['order_number'] == order.order_number


def test_access_with_forged_token_returns_404(motif, product_type, cut, color):
    order = _order(motif, product_type, cut, color)
    token = sign_order_access_token(order.id)
    client = APIClient()

    response = client.get(f'/api/v1/orders/access/{token}tampered')

    assert response.status_code == 404


def test_access_with_expired_token_returns_404(motif, product_type, cut, color, monkeypatch):
    order = _order(motif, product_type, cut, color)
    token = sign_order_access_token(order.id)
    monkeypatch.setattr('core.tokens.ACCESS_TOKEN_MAX_AGE', -1)
    client = APIClient()

    response = client.get(f'/api/v1/orders/access/{token}')

    assert response.status_code == 404


def test_access_with_unknown_order_id_returns_404(motif, product_type, cut, color):
    """A syntactically valid, correctly signed token for an order that no
    longer exists (or never did) must 404 too, not 500.
    """
    import uuid

    token = sign_order_access_token(uuid.uuid4())
    client = APIClient()

    response = client.get(f'/api/v1/orders/access/{token}')

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# fileToken from POST /uploads/designs, consumed by POST /orders
# ---------------------------------------------------------------------------

def _custom_item_body(print_area, file_token):
    return {
        'customer': {'email': 'jana@example.cz', 'firstName': 'Jana', 'lastName': 'Nováková'},
        'billing': {'fullName': 'Jana Nováková', 'street': 'Horní 12', 'city': 'Vrchlabí', 'zip': '54301'},
        'shipping': {'sameAsBilling': True},
        'delivery': {'deliveryType': DeliveryType.PERSONAL_PICKUP},
        'paymentMethod': 'BANK_TRANSFER',
        'termsAccepted': True,
        'items': [{
            'isCustom': True, 'productTypeId': str(print_area.product_type_id), 'cutId': '', 'colorId': '',
            'size': 'M', 'quantity': 1,
            'placements': [{
                'printAreaId': str(print_area.id), 'fileToken': file_token, 'width': 100, 'height': 100,
            }],
        }],
    }


def test_order_with_forged_file_token_is_rejected(print_area, cut, color):
    client = APIClient()
    body = _custom_item_body(print_area, 'not-a-real-token')
    body['items'][0]['cutId'] = str(cut.id)
    body['items'][0]['colorId'] = str(color.id)

    response = client.post('/api/v1/orders', body, format='json')

    assert response.status_code == 422
    assert response.data['error']['code'] == 'INVALID_FILE_TOKEN'


def test_order_with_expired_file_token_is_rejected(print_area, cut, color, monkeypatch):
    token = sign_file_token({'url': '/media/designs/a.png', 'mime_type': 'image/png', 'size': 10})
    monkeypatch.setattr('core.tokens.FILE_TOKEN_MAX_AGE', -1)
    client = APIClient()
    body = _custom_item_body(print_area, token)
    body['items'][0]['cutId'] = str(cut.id)
    body['items'][0]['colorId'] = str(color.id)

    response = client.post('/api/v1/orders', body, format='json')

    assert response.status_code == 422
    assert response.data['error']['code'] == 'INVALID_FILE_TOKEN'


def test_order_with_valid_file_token_from_upload_is_accepted(print_area, cut, color):
    from django.core.files.uploadedfile import SimpleUploadedFile

    png_bytes = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
        b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    client = APIClient()
    upload = client.post(
        '/api/v1/uploads/designs',
        {'file': SimpleUploadedFile('design.png', png_bytes, content_type='image/png')},
        format='multipart',
    )
    assert upload.status_code == 201

    body = _custom_item_body(print_area, upload.data['file_token'])
    body['items'][0]['cutId'] = str(cut.id)
    body['items'][0]['colorId'] = str(color.id)

    response = client.post('/api/v1/orders', body, format='json')

    assert response.status_code == 201