from datetime import timedelta

from django.db.models import Count, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import Size
from shipping.models import Carrier

from .models import Category, Color, Cut, Motif, PrintArea, ProductType
from .serializers import (
    CatalogOptionsResponseSerializer,
    CategorySerializer,
    MotifDetailSerializer,
    MotifListSerializer,
)

RELATED_MOTIFS_LIMIT = 8


@extend_schema_view(
    list=extend_schema(
        tags=['Katalog'],
        summary='Seznam kategorií (menu, homepage)',
        description=(
            'Aktivní kategorie pro navigační menu, ikony na homepage nebo sidebar. '
            'Vždy seřazené podle `position`, pak `name`. `motifCount` je počet '
            '**aktivních** motivů v kategorii (ne všech) - použij pro zobrazení počtu '
            'u názvu kategorie. Neaktivní kategorie se v tomto seznamu nikdy '
            'neobjeví. Bez stránkování (kategorií je málo, vrací se všechny najednou).'
        ),
    ),
    retrieve=extend_schema(
        tags=['Katalog'],
        summary='Detail kategorie podle slugu',
        description=(
            'Pro hlavičku výpisu motivů a breadcrumb. Vyhledává podle `slug`, ne podle '
            '`id`. Pokud kategorie existuje, ale je neaktivní, vrací 404 stejně jako '
            'kdyby neexistovala - frontend nemá jak rozlišit "neexistuje" od '
            '"neaktivní", a ani nemusí.'
        ),
    ),
)
class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /categories, GET /categories/{slug} - storefront navigation."""
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]
    pagination_class = None
    lookup_field = 'slug'
    lookup_value_regex = '[^/]+'

    def get_queryset(self):
        return (
            Category.objects.filter(is_active=True)
            .annotate(motif_count=Count('motifs', filter=Q(motifs__is_active=True)))
            .order_by('position', 'name')
        )


@extend_schema_view(
    list=extend_schema(
        tags=['Katalog'],
        summary='Výpis motivů (katalog, homepage Novinky)',
        description=(
            'Stránkovaný seznam aktivních motivů (24/stránku, `page`/`pageSize`). '
            'Filtry: `category` (slug kategorie, ne id), `q` (fulltext v názvu a '
            'popisu), `ids` (čárkou oddělený seznam UUID - použij pro obnovu košíku '
            'po reloadu stránky, abys dostal aktuální ceny/dostupnost pro konkrétní '
            'motivy). Řazení parametrem `ordering`: `position` (výchozí, ruční pořadí '
            'z administrace), `-createdAt` (nejnovější první, pro homepage "Novinky z '
            'hor"), `price`/`-price`, `name`, `popular` (podle počtu prodaných kusů '
            'za posledních 90 dní). Vrací jen aktivní motivy - neaktivní se v '
            'žádném případě neobjeví, i kdyby odpovídaly filtru.'
        ),
    ),
    retrieve=extend_schema(
        tags=['Katalog'],
        summary='Detail motivu podle slugu (konfigurátor)',
        description=(
            'Plný detail pro stránku motivu / konfigurátor. Obsahuje `description`, '
            'cenu a **všechny** obrázky motivu (ne jen hlavní) - každý obrázek má '
            '`colorId`; frontend podle vybrané barvy ve `/catalog/options` přepne '
            'galerii na obrázky s odpovídajícím `colorId` (nebo ukáže výchozí, pokud '
            'žádný obrázek tu barvu nemá). Vyhledává podle `slug`.'
        ),
    ),
)
class MotifViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /motifs, GET /motifs/{slug}, GET /motifs/{slug}/related."""
    permission_classes = [AllowAny]
    lookup_field = 'slug'
    lookup_value_regex = '[^/]+'
    filter_backends = []  # category/q/ids/ordering are handled explicitly below

    def get_serializer_class(self):
        if self.action == 'list':
            return MotifListSerializer
        return MotifDetailSerializer

    def get_queryset(self):
        queryset = Motif.objects.filter(is_active=True).select_related('category')
        params = self.request.query_params

        category_slug = params.get('category')
        if category_slug:
            queryset = queryset.filter(category__slug=category_slug)

        search_term = params.get('q')
        if search_term:
            queryset = queryset.filter(Q(name__icontains=search_term) | Q(description__icontains=search_term))

        ids_param = params.get('ids')
        if ids_param:
            ids = [value.strip() for value in ids_param.split(',') if value.strip()]
            queryset = queryset.filter(id__in=ids)

        return self._ordered(queryset, params.get('ordering', 'position'))

    @staticmethod
    def _ordered(queryset, ordering):
        if ordering == '-createdAt':
            return queryset.order_by('-created_at')
        if ordering == 'price':
            return queryset.order_by('price')
        if ordering == '-price':
            return queryset.order_by('-price')
        if ordering == 'name':
            return queryset.order_by('name')
        if ordering == 'popular':
            since = timezone.now() - timedelta(days=90)
            return queryset.annotate(
                units_sold=Coalesce(
                    Sum('order_items__quantity', filter=Q(order_items__order__created_at__gte=since)),
                    0,
                )
            ).order_by('-units_sold', 'position')
        return queryset.order_by('position', 'name')

    @extend_schema(
        tags=['Katalog'],
        summary='Doporučené motivy ("Do party se hodí i...")',
        description=(
            'Max 8 aktivních motivů ze **stejné kategorie** jako zadaný motiv, bez '
            'něj samotného, seřazené od nejnovějších. Pokud kategorie nemá dost '
            'motivů na doplnění do 8, zbytek se doplní nejnovějšími motivy z '
            'jakékoli kategorie (tyto filler položky se mohou měnit mezi requesty, '
            'jak přibývají nové motivy). Vrací stejný tvar jako položka ve výpisu '
            '`GET /motifs` (ne plný detail).'
        ),
        responses={200: MotifListSerializer(many=True)},
    )
    @action(detail=True, methods=['get'])
    def related(self, request, slug=None):
        """Max 8 active motifs from the same category, topped up with the
        newest overall if the category doesn't have enough.
        """
        motif = self.get_object()
        same_category = list(
            Motif.objects.filter(is_active=True, category=motif.category)
            .exclude(id=motif.id)
            .order_by('-created_at')[:RELATED_MOTIFS_LIMIT]
        )
        if len(same_category) < RELATED_MOTIFS_LIMIT:
            exclude_ids = [motif.id] + [item.id for item in same_category]
            fillers = Motif.objects.filter(is_active=True).exclude(id__in=exclude_ids).order_by('-created_at')
            same_category += list(fillers[:RELATED_MOTIFS_LIMIT - len(same_category)])

        serializer = MotifListSerializer(same_category, many=True, context=self.get_serializer_context())
        return Response(serializer.data)


class CatalogOptionsView(APIView):
    """GET /catalog/options - every active lookup the configurator/editor/
    checkout needs, in a single call.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['Katalog'],
        summary='Všechny aktivní číselníky jedním voláním (konfigurátor, checkout)',
        description=(
            'Jediné volání, které konfigurátor/editor vlastního potisku/checkout '
            'potřebuje k vykreslení výběru typu oblečení, střihu, barvy, velikosti a '
            'dopravce - místo volání 5 různých endpointů zvlášť. `productTypes` má '
            'zanořené `printAreas` (`widthMm`/`heightMm` v milimetrech - používej pro '
            'rozměry editoru vlastního potisku i validaci umístění na frontendu, i '
            'když finální kontrolu stejně dělá server). `sizes` je pevný seznam '
            'S-XXL, ne z DB. Vrací jen aktivní záznamy. Bezpečné cachovat na pár '
            'minut - mění se jen při úpravě číselníků v administraci.'
        ),
        responses={200: CatalogOptionsResponseSerializer},
    )
    def get(self, request):
        product_types = ProductType.objects.filter(is_active=True).order_by('position', 'name').prefetch_related(
            Prefetch(
                'print_areas',
                queryset=PrintArea.objects.filter(is_active=True).order_by('position', 'name'),
            )
        )
        cuts = Cut.objects.filter(is_active=True).order_by('position', 'name')
        colors = Color.objects.filter(is_active=True).order_by('position', 'name')
        carriers = Carrier.objects.filter(is_active=True).order_by('position', 'name')

        return Response({
            'product_types': [
                {
                    'id': product_type.id,
                    'name': product_type.name,
                    'print_areas': [
                        {
                            'id': area.id,
                            'name': area.name,
                            'width_mm': area.width_mm,
                            'height_mm': area.height_mm,
                        }
                        for area in product_type.print_areas.all()
                    ],
                }
                for product_type in product_types
            ],
            'cuts': [{'id': cut.id, 'name': cut.name} for cut in cuts],
            'colors': [{'id': color.id, 'name': color.name, 'hex_value': color.hex_value} for color in colors],
            'sizes': [{'value': value, 'label': label} for value, label in Size.choices],
            'carriers': [{'id': carrier.id, 'name': carrier.name, 'code': carrier.code} for carrier in carriers],
        })
