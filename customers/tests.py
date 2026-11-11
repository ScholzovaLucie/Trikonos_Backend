import pytest
from rest_framework.test import APIClient

from accounts.models import User

from .models import Customer

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client():
    user = User.objects.create_user(email='admin@trikonos.cz', password='x', name='Admin')
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def customer():
    return Customer.objects.create(
        email='jana@example.cz', first_name='Jana', last_name='Nováková', phone='+420777123456',
        company_name='Firma s.r.o.', marketing_consent=False,
    )


def test_search_matches_company_name(admin_client, customer):
    response = admin_client.get('/api/v1/admin/customers', {'q': 'Firma'})
    assert response.status_code == 200
    assert [item['id'] for item in response.data['results']] == [str(customer.id)]


def test_search_matches_phone(admin_client, customer):
    response = admin_client.get('/api/v1/admin/customers', {'q': '777123456'})
    assert [item['id'] for item in response.data['results']] == [str(customer.id)]


def test_filter_by_marketing_consent_true_excludes_non_consenting(admin_client, customer):
    consenting = Customer.objects.create(email='petr@example.cz', marketing_consent=True)

    response = admin_client.get('/api/v1/admin/customers', {'marketingConsent': 'true'})

    ids = [item['id'] for item in response.data['results']]
    assert str(consenting.id) in ids
    assert str(customer.id) not in ids


def test_can_patch_basic_fields(admin_client, customer):
    response = admin_client.patch(f'/api/v1/admin/customers/{customer.id}', {'note': 'VIP zákaznice'}, format='json')

    assert response.status_code == 200
    customer.refresh_from_db()
    assert customer.note == 'VIP zákaznice'


def test_can_change_email(admin_client, customer):
    response = admin_client.patch(f'/api/v1/admin/customers/{customer.id}', {'email': 'new@example.cz'}, format='json')

    assert response.status_code == 200
    customer.refresh_from_db()
    assert customer.email == 'new@example.cz'


def test_changing_email_to_an_existing_one_returns_409(admin_client, customer):
    Customer.objects.create(email='taken@example.cz')

    response = admin_client.patch(f'/api/v1/admin/customers/{customer.id}', {'email': 'taken@example.cz'}, format='json')

    assert response.status_code == 409
    assert response.data['error']['code'] == 'EMAIL_TAKEN'


def test_export_consents_requires_authentication():
    response = APIClient().get('/api/v1/admin/customers/export-consents')
    assert response.status_code in (401, 403)


def test_export_consents_only_lists_consenting_customers(admin_client, customer):
    consenting = Customer.objects.create(email='souhlas@example.cz', marketing_consent=True)

    response = admin_client.get('/api/v1/admin/customers/export-consents')

    assert response.status_code == 200
    assert response['Content-Type'] == 'text/csv'
    body = response.content.decode('utf-8')
    assert consenting.email in body
    assert customer.email not in body
