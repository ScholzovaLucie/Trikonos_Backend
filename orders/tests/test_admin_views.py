from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from accounts.models import StaffRole, User
from core.exceptions import PacketaApiError
from customers.models import Customer
from orders.models import DeliveryType, Order, OrderStatus, PaymentMethod, ShipmentStatus
from orders.services import build_order_item
from shipping.models import Carrier, PacketaConfig
from shipping.packeta_client import CreatedPacket, PacketStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client():
    user = User.objects.create_user(email='staff@trikonos.cz', password='x', name='Staff', role=StaffRole.STAFF)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def packeta_carrier(db):
    return Carrier.objects.create(code='PACKETA', name='Zásilkovna')


def _order(*, motif, product_type, cut, color, packeta_carrier, delivery_type=DeliveryType.PICKUP_POINT, **extra):
    customer = Customer.objects.create(
        email='jana@example.cz', first_name='Jana', last_name='Nováková', phone='+420777123456',
    )
    fields = {
        'order_number': 'TRK-2026-0001',
        'customer': customer,
        'status': OrderStatus.NEW,
        'subtotal': 59900, 'shipping_cost': 5000, 'total': 64900,
        'carrier': packeta_carrier,
        'delivery_type': delivery_type,
        'pickup_point_id': '131', 'pickup_point_name': 'Vrchlabí, Krkonošská',
        'payment_method': PaymentMethod.BANK_TRANSFER,
        'billing_full_name': 'Jana Nováková', 'billing_street': 'Horní 12', 'billing_city': 'Vrchlabí', 'billing_zip': '54301',
        'shipping_full_name': 'Jana Nováková', 'shipping_street': 'Horní 12', 'shipping_city': 'Vrchlabí', 'shipping_zip': '54301',
    }
    fields.update(extra)
    order = Order.objects.create(**fields)
    build_order_item(order, {
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 1, 'placements': [],
    })
    return order


def test_create_shipment_pickup_point_success(staff_client, motif, product_type, cut, color, packeta_carrier):
    order = _order(
        motif=motif, product_type=product_type, cut=cut, color=color, packeta_carrier=packeta_carrier,
        delivery_type=DeliveryType.PICKUP_POINT,
    )
    created = CreatedPacket(packet_id='12345', barcode='Z12345', barcode_text='Z 123 4567 890')

    with patch('shipping.packeta_client.create_packet', return_value=created) as mock_create:
        response = staff_client.post(f'/api/v1/admin/orders/{order.id}/shipment', {'weight': '0.5'}, format='json')

    assert response.status_code == 200
    assert response.data['tracking_number'] == 'Z 123 4567 890'
    assert response.data['carrier_shipment_id'] == '12345'
    order.refresh_from_db()
    assert order.carrier_shipment_id == '12345'
    assert order.tracking_number == 'Z 123 4567 890'

    sent_attributes = mock_create.call_args.kwargs['packet_attributes']
    assert sent_attributes['addressId'] == '131'
    assert 'street' not in sent_attributes


def test_create_shipment_home_delivery_success(staff_client, motif, product_type, cut, color, packeta_carrier):
    PacketaConfig.objects.update_or_create(pk=1, defaults={'home_delivery_address_id': '106'})
    order = _order(
        motif=motif, product_type=product_type, cut=cut, color=color, packeta_carrier=packeta_carrier,
        delivery_type=DeliveryType.HOME_DELIVERY, pickup_point_id='', pickup_point_name='',
    )
    created = CreatedPacket(packet_id='999', barcode='Z999', barcode_text='Z 999 0000 000')

    with patch('shipping.packeta_client.create_packet', return_value=created) as mock_create:
        response = staff_client.post(f'/api/v1/admin/orders/{order.id}/shipment', {'weight': '1.2'}, format='json')

    assert response.status_code == 200
    sent_attributes = mock_create.call_args.kwargs['packet_attributes']
    assert sent_attributes['addressId'] == '106'
    assert sent_attributes['street'] == 'Horní'
    assert sent_attributes['houseNumber'] == '12'
    assert sent_attributes['city'] == 'Vrchlabí'


def test_create_shipment_home_delivery_without_config_fails(staff_client, motif, product_type, cut, color, packeta_carrier):
    order = _order(
        motif=motif, product_type=product_type, cut=cut, color=color, packeta_carrier=packeta_carrier,
        delivery_type=DeliveryType.HOME_DELIVERY, pickup_point_id='', pickup_point_name='',
    )

    response = staff_client.post(f'/api/v1/admin/orders/{order.id}/shipment', {'weight': '1.0'}, format='json')

    assert response.status_code == 422
    order.refresh_from_db()
    assert order.carrier_shipment_id == ''


def test_create_shipment_carrier_api_error_returns_502(staff_client, motif, product_type, cut, color, packeta_carrier):
    order = _order(motif=motif, product_type=product_type, cut=cut, color=color, packeta_carrier=packeta_carrier)

    with patch('shipping.packeta_client.create_packet', side_effect=PacketaApiError('Neplatné apiPassword.')):
        response = staff_client.post(f'/api/v1/admin/orders/{order.id}/shipment', {'weight': '0.5'}, format='json')

    assert response.status_code == 502
    assert response.data['error']['code'] == 'CARRIER_API_ERROR'
    order.refresh_from_db()
    assert order.carrier_shipment_id == ''


def test_create_shipment_twice_fails(staff_client, motif, product_type, cut, color, packeta_carrier):
    order = _order(
        motif=motif, product_type=product_type, cut=cut, color=color, packeta_carrier=packeta_carrier,
        carrier_shipment_id='111', tracking_number='Z 111',
    )

    response = staff_client.post(f'/api/v1/admin/orders/{order.id}/shipment', {'weight': '0.5'}, format='json')

    assert response.status_code == 409
    assert response.data['error']['code'] == 'SHIPMENT_ALREADY_CREATED'


def test_shipment_label_without_shipment_fails(staff_client, motif, product_type, cut, color, packeta_carrier):
    order = _order(motif=motif, product_type=product_type, cut=cut, color=color, packeta_carrier=packeta_carrier)

    response = staff_client.get(f'/api/v1/admin/orders/{order.id}/shipment/label')

    assert response.status_code == 422
    assert response.data['error']['code'] == 'SHIPMENT_NOT_CREATED'


def test_shipment_label_success_bumps_shipment_status(staff_client, motif, product_type, cut, color, packeta_carrier):
    order = _order(
        motif=motif, product_type=product_type, cut=cut, color=color, packeta_carrier=packeta_carrier,
        carrier_shipment_id='111', tracking_number='Z 111',
    )
    assert order.shipment_status == ShipmentStatus.CREATED

    with patch('shipping.packeta_client.get_label_pdf', return_value=b'%PDF-1.4 fake') as mock_label:
        response = staff_client.get(f'/api/v1/admin/orders/{order.id}/shipment/label')

    assert response.status_code == 200
    assert response['Content-Type'] == 'application/pdf'
    assert response.content == b'%PDF-1.4 fake'
    assert mock_label.call_args.kwargs['packet_id'] == '111'

    order.refresh_from_db()
    assert order.shipment_status == ShipmentStatus.LABEL_PRINTED


def test_shipment_tracking_without_shipment_fails(staff_client, motif, product_type, cut, color, packeta_carrier):
    order = _order(motif=motif, product_type=product_type, cut=cut, color=color, packeta_carrier=packeta_carrier)

    response = staff_client.get(f'/api/v1/admin/orders/{order.id}/shipment/status')

    assert response.status_code == 422
    assert response.data['error']['code'] == 'SHIPMENT_NOT_CREATED'


def test_shipment_tracking_advances_shipment_status(staff_client, motif, product_type, cut, color, packeta_carrier):
    order = _order(
        motif=motif, product_type=product_type, cut=cut, color=color, packeta_carrier=packeta_carrier,
        carrier_shipment_id='111', tracking_number='Z 111', shipment_status=ShipmentStatus.LABEL_PRINTED,
    )
    packeta_status = PacketStatus(status_code=4, code_text='departed', status_text='Packet is on the way.')

    with patch('shipping.packeta_client.get_packet_status', return_value=packeta_status) as mock_status:
        response = staff_client.get(f'/api/v1/admin/orders/{order.id}/shipment/status')

    assert response.status_code == 200
    assert response.data['shipment_status'] == ShipmentStatus.IN_TRANSIT
    assert mock_status.call_args.kwargs['packet_id'] == '111'
    order.refresh_from_db()
    assert order.shipment_status == ShipmentStatus.IN_TRANSIT


def test_shipment_tracking_never_regresses_shipment_status(staff_client, motif, product_type, cut, color, packeta_carrier):
    order = _order(
        motif=motif, product_type=product_type, cut=cut, color=color, packeta_carrier=packeta_carrier,
        carrier_shipment_id='111', tracking_number='Z 111', shipment_status=ShipmentStatus.DELIVERED,
    )
    # Packeta reporting an earlier state (e.g. a stale/out-of-order poll)
    # must never move shipment_status backwards.
    packeta_status = PacketStatus(status_code=2, code_text='arrived', status_text='Arrived at branch.')

    with patch('shipping.packeta_client.get_packet_status', return_value=packeta_status):
        response = staff_client.get(f'/api/v1/admin/orders/{order.id}/shipment/status')

    assert response.status_code == 200
    assert response.data['shipment_status'] == ShipmentStatus.DELIVERED
    order.refresh_from_db()
    assert order.shipment_status == ShipmentStatus.DELIVERED
