import pytest

from catalog.models import Category, Color, Cut, PrintArea, ProductType, Motif


@pytest.fixture
def category(db):
    return Category.objects.create(name='Pánská trička', slug='panska-tricka')


@pytest.fixture
def product_type(db):
    return ProductType.objects.create(name='Tričko')


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
