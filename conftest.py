import pytest
from django.core.cache import cache

from catalog.models import Category, Color, Cut, PrintArea, ProductType, Motif
from shipping.models import Carrier


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """DRF's ScopedRateThrottle counts requests via the Django cache, which
    is a process-global dict (LocMemCache) untouched by pytest-django's
    per-test DB rollback - without this, request counts leak across tests
    sharing a scope (e.g. 'public-write') and later tests start failing
    with 429 depending on how many other tests ran first.
    """
    cache.clear()


@pytest.fixture
def carrier(db):
    return Carrier.objects.create(code='PACKETA', name='Zásilkovna')


@pytest.fixture
def category(db):
    return Category.objects.create(name='Pánská trička', slug='panska-tricka')


@pytest.fixture
def product_type(db):
    return ProductType.objects.create(name='Tričko', price=500)


@pytest.fixture
def print_area(db, product_type):
    return PrintArea.objects.create(product_type=product_type, name='Přední strana', width_mm=300, height_mm=400)


@pytest.fixture
def cut(db):
    return Cut.objects.create(name='Pánské')


@pytest.fixture
def color(db):
    return Color.objects.create(name='Černá', hex_value='#000000')


@pytest.fixture
def motif(db, category):
    return Motif.objects.create(name='Bitcoin', slug='bitcoin', price=59900, category=category)
