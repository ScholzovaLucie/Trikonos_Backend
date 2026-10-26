import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from accounts.models import StaffRole
from catalog.models import Category, Color, Cut, Motif, PrintArea, ProductType
from shipping.models import Carrier, ShippingPrice, ShippingQuantityDiscount

User = get_user_model()

CUTS = ['Pánské', 'Dámské', 'Unisex']

COLORS = [
    ('Černá', '#000000'),
    ('Bílá', '#FFFFFF'),
    ('Šedá', '#808080'),
    ('Námořnická modrá', '#1B2A4A'),
    ('Červená', '#C0392B'),
    ('Zelená', '#2E7D32'),
]

PRODUCT_TYPES_WITH_PRINT_AREAS = {
    'Tričko': [
        ('Přední strana', 300, 400),
        ('Zadní strana', 300, 400),
        ('Levý rukáv', 80, 150),
        ('Pravý rukáv', 80, 150),
    ],
    'Mikina': [
        ('Přední strana', 300, 400),
        ('Zadní strana', 300, 400),
        ('Levý rukáv', 80, 150),
        ('Pravý rukáv', 80, 150),
        ('Kapuce', 150, 150),
    ],
    'Tílko': [
        ('Přední strana', 280, 380),
        ('Zadní strana', 280, 380),
    ],
    'Doplněk': [
        ('Přední strana', 100, 100),
    ],
}

CATEGORIES = ['Pánská trička', 'Dámská trička', 'Mikiny', 'Doplňky', 'Kolekce', 'Týmové akce']

SHIPPING_PRICE = 5000  # 50 Kč

SHIPPING_QUANTITY_DISCOUNTS = [
    (30, 30),  # 30+ ks -> -30 %
    (50, 100),  # 50+ ks -> zdarma
]

TEST_MOTIFS = [
    ('Bitcoin', 'Pánská trička', 59900, 'Minimalistický symbol Bitcoinu.'),
    ('Krkonoše - západ slunce', 'Kolekce', 64900, 'Silueta hor při večerním slunci.'),
    ('Medvěd', 'Dámská trička', 59900, 'Ilustrovaný medvěd v lese.'),
    ('Vlk', 'Pánská trička', 59900, 'Grafický vlk v běhu.'),
    ('Horská kola', 'Kolekce', 64900, 'Pro nadšence do cyklistiky v horách.'),
    ('Sněžka', 'Kolekce', 64900, 'Nejvyšší hora Česka jako minimalistická grafika.'),
    ('Káva první', 'Dámská trička', 54900, 'Ironický nápis pro ranní ptáčata.'),
    ('Retro gradient', 'Mikiny', 89900, 'Barevný gradient v retro stylu.'),
    ('Outdoor logo', 'Doplňky', 39900, 'Jednoduché logo pro doplňky a kšiltovky.'),
    ('Týmový duch', 'Týmové akce', 59900, 'Základní motiv pro firemní a týmové objednávky.'),
]


class Command(BaseCommand):
    help = 'Seed lookup tables, the Zásilkovna carrier, the first admin user and test motifs.'

    @transaction.atomic
    def handle(self, *args, **options):
        self._seed_cuts()
        self._seed_colors()
        product_types = self._seed_product_types()
        self._seed_print_areas(product_types)
        categories = self._seed_categories()
        self._seed_carrier()
        self._seed_shipping_price()
        self._seed_admin()
        self._seed_motifs(categories)
        self.stdout.write(self.style.SUCCESS('Seed data ready.'))

    def _seed_cuts(self):
        for position, name in enumerate(CUTS):
            Cut.objects.update_or_create(name=name, defaults={'position': position})

    def _seed_colors(self):
        for position, (name, hex_value) in enumerate(COLORS):
            Color.objects.update_or_create(
                name=name, defaults={'hex_value': hex_value, 'position': position},
            )

    def _seed_product_types(self):
        product_types = {}
        for position, name in enumerate(PRODUCT_TYPES_WITH_PRINT_AREAS):
            product_types[name], _ = ProductType.objects.update_or_create(
                name=name, defaults={'position': position},
            )
        return product_types

    def _seed_print_areas(self, product_types):
        for type_name, areas in PRODUCT_TYPES_WITH_PRINT_AREAS.items():
            product_type = product_types[type_name]
            for position, (name, width_mm, height_mm) in enumerate(areas):
                PrintArea.objects.update_or_create(
                    product_type=product_type,
                    name=name,
                    defaults={'width_mm': width_mm, 'height_mm': height_mm, 'position': position},
                )

    def _seed_categories(self):
        categories = {}
        for position, name in enumerate(CATEGORIES):
            categories[name], _ = Category.objects.update_or_create(
                name=name, defaults={'slug': slugify(name), 'position': position},
            )
        return categories

    def _seed_carrier(self):
        Carrier.objects.update_or_create(
            code='PACKETA', defaults={'name': 'Zásilkovna', 'position': 0},
        )

    def _seed_shipping_price(self):
        price = ShippingPrice.get_solo()
        if price.price is None:
            price.price = SHIPPING_PRICE
            price.save()

        for position, (min_quantity, discount_percent) in enumerate(SHIPPING_QUANTITY_DISCOUNTS):
            ShippingQuantityDiscount.objects.update_or_create(
                min_quantity=min_quantity, defaults={'discount_percent': discount_percent, 'position': position},
            )

    def _seed_admin(self):
        email = os.environ.get('ADMIN_EMAIL')
        password = os.environ.get('ADMIN_PASSWORD')
        name = os.environ.get('ADMIN_NAME', 'Admin')

        if not email or not password:
            self.stdout.write(self.style.WARNING(
                'ADMIN_EMAIL / ADMIN_PASSWORD not set in env - skipping admin user seed.'
            ))
            return

        if User.objects.filter(email__iexact=email).exists():
            return

        User.objects.create_superuser(
            email=email.lower(), password=password, name=name, role=StaffRole.ADMIN,
        )
        self.stdout.write(self.style.SUCCESS(f'Created admin user {email}.'))

    def _seed_motifs(self, categories):
        if Motif.objects.exists():
            return

        for position, (name, category_name, price, description) in enumerate(TEST_MOTIFS):
            Motif.objects.create(
                name=name,
                slug=slugify(name),
                description=description,
                price=price,
                category=categories[category_name],
                position=position,
                images=[],
            )
