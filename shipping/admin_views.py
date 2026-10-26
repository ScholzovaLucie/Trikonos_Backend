from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.response import Response

from accounts.permissions import IsAdmin, IsStaffOrAdmin
from core.admin_lookup import LookupAdminViewSet, lookup_schema_view

from .admin_serializers import (
    AdminCarrierSerializer,
    AdminPacketaConfigSerializer,
    AdminShippingPriceSerializer,
    AdminShippingQuantityDiscountSerializer,
)
from .models import Carrier, PacketaConfig, ShippingPrice, ShippingQuantityDiscount


@lookup_schema_view(
    'dopravce', 'dopravců',
    extra_note=(
        '`code` se uloží velkými písmeny (interní kód pro integrační vrstvu, '
        'např. PACKETA). Cena dopravy je jednotná pro celý e-shop (viz '
        '`GET/PUT /admin/shipping-price` a `/admin/shipping-quantity-discounts`), '
        'nezávisí na dopravci - tahle tabulka slouží jen k výběru dopravce '
        'v checkoutu a u objednávky.'
    ),
)
class AdminCarrierViewSet(LookupAdminViewSet):
    """Dopravci (Zásilkovna, DPD, ...), nabízení zákazníkovi při checkoutu."""
    queryset = Carrier.objects.all()
    serializer_class = AdminCarrierSerializer
    search_fields = ['name', 'code']


@extend_schema_view(
    list=extend_schema(
        tags=['Admin - číselníky'],
        summary='Zobrazit cenu dopravy',
        description=(
            'Jedna cena dopravy platná pro celý e-shop (haléře), bez ohledu na '
            'dopravce nebo typ doručení. `price: null` znamená, že ji admin '
            'ještě nenastavil - dokud se nenastaví, `POST /orders` nechá '
            '`shippingCost` prázdné a cenu dopravy nastavuje admin ručně u '
            'každé objednávky, jako doteď.'
        ),
        responses={200: AdminShippingPriceSerializer},
    ),
    update=extend_schema(
        tags=['Admin - číselníky'],
        summary='Nastavit cenu dopravy',
        description='Jen role ADMIN. `price` v haléřích (celé číslo, `0` = doprava vždy zdarma).',
        request=AdminShippingPriceSerializer,
        responses={200: AdminShippingPriceSerializer},
    ),
)
class AdminShippingPriceView(viewsets.ViewSet):
    """GET/PUT /admin/shipping-price - singleton cena dopravy pro celý e-shop."""

    def get_permissions(self):
        if self.request.method == 'GET':
            return [IsStaffOrAdmin()]
        return [IsAdmin()]

    def list(self, request):
        serializer = AdminShippingPriceSerializer(ShippingPrice.get_solo())
        return Response(serializer.data)

    def update(self, request):
        instance = ShippingPrice.get_solo()
        serializer = AdminShippingPriceSerializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        tags=['Admin - číselníky'],
        summary='Zobrazit nastavení Packety',
        description=(
            'Přihlašovací údaje a nastavení pro napojení na Packeta '
            '(Zásilkovna) API (viz `shipping/packeta_client.py`) - jen role '
            'ADMIN, jsou to citlivé údaje. `apiPassword: ""` znamená, že '
            'integrace ještě není nastavená - vytvoření zásilky pak selže '
            's jasnou chybou. `homeDeliveryAddressId` je ID externího '
            'dopravce pro doručení na adresu (ne ID výdejního místa) - '
            'dostanete ho z klientské zóny Packety, nejde ho uhodnout. Pro '
            'doručení na výdejní místo stačí jen `apiPassword`.'
        ),
        responses={200: AdminPacketaConfigSerializer},
    ),
    update=extend_schema(
        tags=['Admin - číselníky'],
        summary='Nastavit napojení na Packetu',
        description='Jen role ADMIN.',
        request=AdminPacketaConfigSerializer,
        responses={200: AdminPacketaConfigSerializer},
    ),
)
class AdminPacketaConfigView(viewsets.ViewSet):
    """GET/PUT /admin/packeta-config - singleton nastavení napojení na Packetu."""
    permission_classes = [IsAdmin]

    def list(self, request):
        serializer = AdminPacketaConfigSerializer(PacketaConfig.get_solo())
        return Response(serializer.data)

    def update(self, request):
        instance = PacketaConfig.get_solo()
        serializer = AdminPacketaConfigSerializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


@lookup_schema_view(
    'slevový práh', 'slevových prahů',
    extra_note=(
        '`minQuantity` je celkový počet kusů v objednávce (součet `quantity` '
        'přes všechny položky), od kterého práh platí. `discountPercent` '
        '0-100 (`100` = doprava od tohoto počtu kusů zdarma) se odečítá ze '
        'základní ceny dopravy (`GET /admin/shipping-price`). Platí práh s '
        'nejvyšším `minQuantity`, který objednávka splňuje; bez shody platí '
        'plná cena.'
    ),
)
class AdminShippingQuantityDiscountViewSet(LookupAdminViewSet):
    """Slevové prahy dopravy podle počtu kusů v objednávce (např. 30+ ks -30 %, 50+ ks zdarma)."""
    queryset = ShippingQuantityDiscount.objects.all()
    serializer_class = AdminShippingQuantityDiscountSerializer

    def get_queryset(self):
        # Not calling super().get_queryset(): LookupAdminViewSet orders by
        # ('position', 'name') which this model doesn't have - order by the
        # quantity threshold instead, which is what actually determines
        # which tier applies.
        queryset = self.queryset
        is_active = self.request.query_params.get('isActive')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() in ('1', 'true', 'yes'))

        return queryset.order_by('min_quantity')
