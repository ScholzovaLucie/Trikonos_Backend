from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.response import Response

from accounts.permissions import IsAdmin, IsStaffOrAdmin
from core.admin_lookup import LookupAdminViewSet, lookup_schema_view

from .admin_serializers import (
    AdminCarrierSerializer,
    AdminCodSurchargeSerializer,
    AdminPacketaConfigSerializer,
    AdminShippingPriceSerializer,
    AdminShippingQuantityDiscountSerializer,
)
from .models import Carrier, CodSurcharge, PacketaConfig, ShippingPrice, ShippingQuantityDiscount


@lookup_schema_view(
    'dopravce', 'dopravců',
    extra_note=(
        '`code` se uloží velkými písmeny (interní kód pro integrační vrstvu, '
        'např. PACKETA). Cena dopravy se nastavuje zvlášť pro každou '
        'kombinaci dopravce a typu doručení (viz `/admin/shipping-prices`) - '
        'tahle tabulka slouží jen k výběru dopravce v checkoutu a u '
        'objednávky.'
    ),
    tag='Admin - doprava',
)
class AdminCarrierViewSet(LookupAdminViewSet):
    """Dopravci (Zásilkovna, DPD, ...), nabízení zákazníkovi při checkoutu."""
    queryset = Carrier.objects.all()
    serializer_class = AdminCarrierSerializer
    search_fields = ['name', 'code']


@lookup_schema_view(
    'cena dopravy', 'cen dopravy',
    extra_note=(
        'Jedna cena (haléře) pro každou kombinaci dopravce a typu doručení '
        '(`PICKUP_POINT`/`HOME_DELIVERY` - `PERSONAL_PICKUP` sem nepatří, je '
        'vždy zdarma bez ohledu na cokoliv tady nastavené). Bez řádku pro '
        'danou kombinaci zůstává `shippingCost` u objednávky/košíku `null` a '
        'cenu dopravy nastavuje admin ručně, jako doteď. Duplicitní '
        'kombinace dopravce + typu doručení vrátí 400.'
    ),
    tag='Admin - doprava',
)
class AdminShippingPriceViewSet(LookupAdminViewSet):
    """Ceny dopravy podle dopravce a typu doručení."""
    queryset = ShippingPrice.objects.select_related('carrier').all()
    serializer_class = AdminShippingPriceSerializer

    def get_queryset(self):
        # Not calling super().get_queryset(): LookupAdminViewSet orders by
        # ('position', 'name') which this model doesn't have.
        queryset = self.queryset
        carrier_id = self.request.query_params.get('carrier')
        if carrier_id:
            queryset = queryset.filter(carrier_id=carrier_id)
        return queryset.order_by('carrier__name', 'delivery_type')


@extend_schema_view(
    list=extend_schema(
        tags=['Admin - doprava'],
        summary='Zobrazit příplatek za dobírku',
        description=(
            'Jednotný příplatek (haléře) připočítaný k `shippingCost`, '
            'kdykoli je `paymentMethod: CASH_ON_DELIVERY` - dopravce si za '
            'výběr hotovosti účtuje poplatek, tohle umožňuje ho přenést na '
            'zákazníka. `amount: 0` (výchozí) = žádný příplatek. Nikdy se '
            'nepřičítá u `PERSONAL_PICKUP` (osobní odběr je vždy zdarma, '
            'žádný dopravce peníze nevybírá).'
        ),
        responses={200: AdminCodSurchargeSerializer},
    ),
    update=extend_schema(
        tags=['Admin - doprava'],
        summary='Nastavit příplatek za dobírku',
        description='Jen role ADMIN.',
        request=AdminCodSurchargeSerializer,
        responses={200: AdminCodSurchargeSerializer},
    ),
)
class AdminCodSurchargeView(viewsets.ViewSet):
    """GET/PUT /admin/cod-surcharge - singleton příplatek za dobírku."""

    def get_permissions(self):
        if self.request.method == 'GET':
            return [IsStaffOrAdmin()]
        return [IsAdmin()]

    def list(self, request):
        serializer = AdminCodSurchargeSerializer(CodSurcharge.get_solo())
        return Response(serializer.data)

    def update(self, request):
        instance = CodSurcharge.get_solo()
        serializer = AdminCodSurchargeSerializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        tags=['Admin - doprava'],
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
        tags=['Admin - doprava'],
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
        '`shippingPriceId` (povinné) určuje, ke které kombinaci dopravce + '
        'typu doručení práh patří - každá kombinace má svou vlastní sadu '
        'prahů (viz `/admin/shipping-prices`). `minQuantity` je celkový '
        'počet kusů v objednávce (součet `quantity` přes všechny položky), '
        'od kterého práh platí, unikátní jen uvnitř jedné `shippingPriceId`. '
        '`discountPercent` 0-100 (`100` = doprava od tohoto počtu kusů '
        'zdarma) se odečítá ze základní ceny té kombinace. Platí práh s '
        'nejvyšším `minQuantity`, který objednávka splňuje; bez shody platí '
        'plná cena. Duplicitní `minQuantity` v rámci jedné `shippingPriceId` '
        'vrátí 409 (jinak by nebylo jednoznačné, který práh platí).'
    ),
    tag='Admin - doprava',
)
class AdminShippingQuantityDiscountViewSet(LookupAdminViewSet):
    """Slevové prahy dopravy podle počtu kusů v objednávce, pro danou kombinaci dopravce + typu doručení."""
    queryset = ShippingQuantityDiscount.objects.select_related('shipping_price__carrier').all()
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
        shipping_price_id = self.request.query_params.get('shippingPriceId')
        if shipping_price_id:
            queryset = queryset.filter(shipping_price_id=shipping_price_id)

        return queryset.order_by('min_quantity')
