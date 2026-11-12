from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from customers.models import Customer
from orders.models import Order, OrderStatus
from orders.services import build_order_item

from .models import Category, Motif, MotifVariant, PrintArea, ProductType

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client():
    user = User.objects.create_user(email='admin@trikonos.cz', password='x', name='Admin')
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _fake_jpeg():
    buffer = BytesIO()
    Image.new('RGB', (800, 600), color='red').save(buffer, format='JPEG')
    buffer.seek(0)
    return SimpleUploadedFile('photo.jpg', buffer.read(), content_type='image/jpeg')


def test_upload_image_creates_thumbnails_and_becomes_the_main_image(admin_client, motif, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path

    response = admin_client.post(
        f'/api/v1/admin/motifs/{motif.id}/images', {'file': _fake_jpeg()}, format='multipart',
    )

    assert response.status_code == 201
    images = response.data['images']
    assert len(images) == 1
    image = images[0]
    assert image['url']
    assert image['thumbnail_small_url']
    assert image['thumbnail_large_url']

    motif.refresh_from_db()
    stored = motif.images[0]
    assert default_storage.exists(stored['path'])
    assert default_storage.exists(stored['thumbnail_small_path'])
    assert default_storage.exists(stored['thumbnail_large_path'])


def test_unauthenticated_cannot_upload_image(motif, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path

    response = APIClient().post(
        f'/api/v1/admin/motifs/{motif.id}/images', {'file': _fake_jpeg()}, format='multipart',
    )

    assert response.status_code in (401, 403)


def test_upload_rejects_file_over_size_limit(admin_client, motif, monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setattr('catalog.admin_motif_serializers.MOTIF_IMAGE_MAX_SIZE', 10)

    response = admin_client.post(
        f'/api/v1/admin/motifs/{motif.id}/images', {'file': _fake_jpeg()}, format='multipart',
    )

    assert response.status_code == 413
    assert response.data['error']['code'] == 'FILE_TOO_LARGE'


def test_upload_rejects_non_image_file(admin_client, motif, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    fake_file = SimpleUploadedFile('not-a-photo.jpg', b'this is not an image', content_type='image/jpeg')

    response = admin_client.post(f'/api/v1/admin/motifs/{motif.id}/images', {'file': fake_file}, format='multipart')

    assert response.status_code == 400


def test_reorder_images_changes_the_main_image(admin_client, motif, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    first = admin_client.post(f'/api/v1/admin/motifs/{motif.id}/images', {'file': _fake_jpeg()}, format='multipart')
    second = admin_client.post(f'/api/v1/admin/motifs/{motif.id}/images', {'file': _fake_jpeg()}, format='multipart')
    first_id = first.data['images'][0]['id']
    second_id = second.data['images'][1]['id']

    response = admin_client.post(
        f'/api/v1/admin/motifs/{motif.id}/images/reorder', {'ids': [second_id, first_id]}, format='json',
    )

    assert response.status_code == 200
    assert [image['id'] for image in response.data['images']] == [second_id, first_id]


def test_delete_image_removes_files_from_storage(admin_client, motif, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    upload = admin_client.post(f'/api/v1/admin/motifs/{motif.id}/images', {'file': _fake_jpeg()}, format='multipart')
    motif.refresh_from_db()
    stored = motif.images[0]
    image_id = upload.data['images'][0]['id']

    response = admin_client.delete(f'/api/v1/admin/motifs/{motif.id}/images/{image_id}')

    assert response.status_code == 204
    motif.refresh_from_db()
    assert motif.images == []
    assert not default_storage.exists(stored['path'])
    assert not default_storage.exists(stored['thumbnail_small_path'])
    assert not default_storage.exists(stored['thumbnail_large_path'])


def test_delete_unknown_image_returns_404(admin_client, motif):
    response = admin_client.delete(f'/api/v1/admin/motifs/{motif.id}/images/00000000-0000-0000-0000-000000000000')
    assert response.status_code == 404


def test_update_image_alt_text(admin_client, motif, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    upload = admin_client.post(f'/api/v1/admin/motifs/{motif.id}/images', {'file': _fake_jpeg()}, format='multipart')
    image_id = upload.data['images'][0]['id']

    response = admin_client.patch(
        f'/api/v1/admin/motifs/{motif.id}/images/{image_id}', {'altText': 'Bitcoin tričko'}, format='json',
    )

    assert response.status_code == 200
    assert response.data['images'][0]['alt_text'] == 'Bitcoin tričko'


def test_deleting_a_motif_used_in_an_order_returns_409(motif, product_type, cut, color, admin_client):
    customer = Customer.objects.create(email='jana@example.cz', first_name='Jana', last_name='Nováková')
    order = Order.objects.create(
        order_number='TRK-2026-9001', customer=customer, status=OrderStatus.NEW,
        billing_full_name='Jana Nováková', billing_street='Horní 12', billing_city='Vrchlabí', billing_zip='54301',
        shipping_full_name='Jana Nováková', shipping_street='Horní 12', shipping_city='Vrchlabí', shipping_zip='54301',
    )
    build_order_item(order, {
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': 1, 'placements': [],
    })

    response = admin_client.delete(f'/api/v1/admin/motifs/{motif.id}')

    assert response.status_code == 409
    assert response.data['error']['code'] == 'IN_USE'
    assert Motif.objects.filter(pk=motif.id).exists()


def test_deleting_an_unused_motif_succeeds(motif, admin_client):
    response = admin_client.delete(f'/api/v1/admin/motifs/{motif.id}')

    assert response.status_code == 204
    assert not Motif.objects.filter(pk=motif.id).exists()


# ---------------------------------------------------------------------------
# Public endpoints - ordering=popular (90-day window) and related() filler
# ---------------------------------------------------------------------------

def _sell(motif, product_type, cut, color, *, quantity, created_at=None):
    MotifVariant.objects.get_or_create(motif=motif, product_type=product_type, cut=cut, color=color, size='M')
    customer = Customer.objects.create(email=f'{motif.slug}-{quantity}@example.cz')
    order = Order.objects.create(
        order_number=f'TRK-2026-{motif.slug}-{quantity}', customer=customer, status=OrderStatus.NEW,
        billing_full_name='Jana', billing_street='Horní 12', billing_city='Vrchlabí', billing_zip='54301',
        shipping_full_name='Jana', shipping_street='Horní 12', shipping_city='Vrchlabí', shipping_zip='54301',
    )
    build_order_item(order, {
        'is_custom': False, 'motif': motif, 'custom_text': '', 'product_type': product_type,
        'cut': cut, 'color': color, 'size': 'M', 'quantity': quantity, 'placements': [],
    })
    if created_at is not None:
        Order.objects.filter(pk=order.pk).update(created_at=created_at)
    return order


def test_motifs_ordering_popular_ranks_by_recent_units_sold(category, product_type, cut, color):
    big_seller = Motif.objects.create(name='Hory', slug='hory', price=50000, category=category)
    small_seller = Motif.objects.create(name='Les', slug='les', price=50000, category=category)
    _sell(big_seller, product_type, cut, color, quantity=10)
    _sell(small_seller, product_type, cut, color, quantity=1)

    response = APIClient().get('/api/v1/motifs', {'ordering': 'popular'})

    slugs = [item['slug'] for item in response.data['results']]
    assert slugs.index('hory') < slugs.index('les')


def test_motifs_ordering_popular_ignores_sales_older_than_90_days(category, product_type, cut, color):
    recently_popular = Motif.objects.create(name='Hory', slug='hory', price=50000, category=category)
    once_popular = Motif.objects.create(name='Les', slug='les', price=50000, category=category)
    _sell(recently_popular, product_type, cut, color, quantity=5)
    _sell(once_popular, product_type, cut, color, quantity=50, created_at=timezone.now() - timedelta(days=91))

    response = APIClient().get('/api/v1/motifs', {'ordering': 'popular'})

    slugs = [item['slug'] for item in response.data['results']]
    assert slugs.index('hory') < slugs.index('les')


def test_related_motifs_prefers_same_category(motif, category):
    # Below RELATED_MOTIFS_LIMIT (8), so the other-category motif still
    # shows up too - just as filler, after the same-category one.
    same_category = Motif.objects.create(name='Jiný motiv', slug='jiny-motiv', price=50000, category=category)
    other_category = Category.objects.create(name='Dámská', slug='damska')
    filler = Motif.objects.create(name='Mimo kategorii', slug='mimo-kategorii', price=50000, category=other_category)

    response = APIClient().get(f'/api/v1/motifs/{motif.slug}/related')

    slugs = [item['slug'] for item in response.data]
    assert slugs == [same_category.slug, filler.slug]


def test_related_motifs_fills_up_to_limit_from_other_categories(motif, category):
    other_category = Category.objects.create(name='Dámská', slug='damska')
    fillers = [
        Motif.objects.create(name=f'Filler {i}', slug=f'filler-{i}', price=50000, category=other_category)
        for i in range(8)
    ]

    response = APIClient().get(f'/api/v1/motifs/{motif.slug}/related')

    assert len(response.data) == 8
    returned_slugs = {item['slug'] for item in response.data}
    assert returned_slugs == {f.slug for f in fillers}


def test_related_motifs_excludes_inactive_motifs(motif, category):
    Motif.objects.create(name='Neaktivní', slug='neaktivni', price=50000, category=category, is_active=False)

    response = APIClient().get(f'/api/v1/motifs/{motif.slug}/related')

    assert response.data == []


def test_inactive_motif_returns_404_on_public_detail(motif):
    motif.is_active = False
    motif.save(update_fields=['is_active'])

    response = APIClient().get(f'/api/v1/motifs/{motif.slug}')

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /catalog/options - public lookups must never leak inactive records
# ---------------------------------------------------------------------------

def test_catalog_options_excludes_inactive_cut_color_and_product_type(product_type, cut, color):
    cut.is_active = False
    cut.save(update_fields=['is_active'])
    color.is_active = False
    color.save(update_fields=['is_active'])
    product_type.is_active = False
    product_type.save(update_fields=['is_active'])

    response = APIClient().get('/api/v1/catalog/options')

    assert response.data['cuts'] == []
    assert response.data['colors'] == []
    assert response.data['product_types'] == []


def test_catalog_options_excludes_inactive_print_area_of_an_active_product_type(product_type, print_area):
    print_area.is_active = False
    print_area.save(update_fields=['is_active'])

    response = APIClient().get('/api/v1/catalog/options')

    product_type_data = next(pt for pt in response.data['product_types'] if pt['id'] == product_type.id)
    assert product_type_data['print_areas'] == []


# ---------------------------------------------------------------------------
# Generic lookup tables (categories/colors/cuts/product types/print areas)
# share one deletion-protection path (core.admin_lookup.LookupAdminViewSet) -
# exercised here through a category, since motif's own (different) delete
# view already covers the motif-specific IN_USE case above.
# ---------------------------------------------------------------------------

def test_deleting_a_category_used_by_a_motif_returns_409(motif, category, admin_client):
    response = admin_client.delete(f'/api/v1/admin/categories/{category.id}')

    assert response.status_code == 409
    assert response.data['error']['code'] == 'IN_USE'
    assert Category.objects.filter(id=category.id).exists()


# ---------------------------------------------------------------------------
# No N+1 queries on list endpoints
# ---------------------------------------------------------------------------

def test_motif_list_query_count_does_not_grow_with_motif_count(category, django_assert_max_num_queries):
    for i in range(5):
        Motif.objects.create(name=f'Motif {i}', slug=f'motif-{i}', price=10000, category=category)

    with django_assert_max_num_queries(4):
        response = APIClient().get('/api/v1/motifs')

    assert response.status_code == 200
    assert len(response.data['results']) == 5


def test_catalog_options_query_count_does_not_grow_with_product_type_count(django_assert_max_num_queries):
    for i in range(5):
        pt = ProductType.objects.create(name=f'Typ {i}')
        PrintArea.objects.create(product_type=pt, name='Přední strana', width_mm=300, height_mm=400)

    with django_assert_max_num_queries(6):
        response = APIClient().get('/api/v1/catalog/options')

    assert response.status_code == 200
    assert len(response.data['product_types']) == 5


# ---------------------------------------------------------------------------
# Motif availability matrix (admin CRUD + public exposure)
# ---------------------------------------------------------------------------

def test_motif_detail_exposes_variants(motif, product_type, cut, color):
    response = APIClient().get(f'/api/v1/motifs/{motif.slug}')

    assert response.status_code == 200
    assert response.data['variants'] == [{
        'product_type_id': str(product_type.id), 'cut_id': str(cut.id), 'color_id': str(color.id), 'size': 'M',
    }]


def test_admin_can_list_motif_variants_for_a_motif(admin_client, motif, product_type, cut, color):
    response = admin_client.get('/api/v1/admin/motif-variants', {'motif': str(motif.id)})

    assert response.status_code == 200
    assert len(response.data['results']) == 1


def test_listing_motif_variants_without_motif_param_returns_400(admin_client):
    response = admin_client.get('/api/v1/admin/motif-variants')
    assert response.status_code == 400


def test_admin_can_create_motif_variant(admin_client, motif, product_type, cut, color):
    response = admin_client.post('/api/v1/admin/motif-variants', {
        'motifId': str(motif.id), 'productTypeId': str(product_type.id),
        'cutId': str(cut.id), 'colorId': str(color.id), 'size': 'L',
    }, format='json')

    assert response.status_code == 201
    assert MotifVariant.objects.filter(motif=motif, size='L').exists()


def test_duplicate_motif_variant_returns_409(admin_client, motif, product_type, cut, color):
    response = admin_client.post('/api/v1/admin/motif-variants', {
        'motifId': str(motif.id), 'productTypeId': str(product_type.id),
        'cutId': str(cut.id), 'colorId': str(color.id), 'size': 'M',
    }, format='json')

    assert response.status_code == 409
    assert response.data['error']['code'] == 'DUPLICATE_VARIANT'


def test_admin_can_delete_motif_variant_and_it_stops_being_orderable(admin_client, motif, product_type, cut, color):
    variant = MotifVariant.objects.get(motif=motif, product_type=product_type, cut=cut, color=color, size='M')

    response = admin_client.delete(f'/api/v1/admin/motif-variants/{variant.id}')
    assert response.status_code == 204

    cart_response = admin_client.post('/api/v1/cart/validate', {'items': [{
        'isCustom': False, 'motifId': str(motif.id), 'productTypeId': str(product_type.id),
        'cutId': str(cut.id), 'colorId': str(color.id), 'size': 'M', 'quantity': 1,
    }]}, format='json')
    assert cart_response.data['items'][0]['valid'] is False
