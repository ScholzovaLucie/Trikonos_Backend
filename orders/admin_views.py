from django.db import transaction
from django.db.models import BooleanField, Case, Count, Q, When
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, NotFound
from rest_framework.response import Response

from accounts.models import StaffRole
from accounts.permissions import IsStaffOrAdmin

from . import services
from .admin_serializers import (
    LOCKED_UPDATE_FIELDS,
    AdminConfirmOrderSerializer,
    AdminCreateShipmentSerializer,
    AdminOrderDetailSerializer,
    AdminOrderItemPriceSerializer,
    AdminOrderListSerializer,
    AdminOrderStatusSerializer,
    AdminOrderUpdateSerializer,
    AdminPaymentStatusSerializer,
    AdminPlacementUpdateSerializer,
    AdminResendEmailSerializer,
    AdminShipmentStatusSerializer,
    AdminShippingCostSerializer,
)
from .models import Order, OrderStatus, PaymentStatus, ShipmentStatus
from .serializers import OrderCreateSerializer

_TAG = ['Admin - objednávky']

_ORDERING_MAP = {
    '-createdAt': '-created_at',
    'createdAt': 'created_at',
    'total': 'total',
    '-total': '-total',
    'orderNumber': 'order_number',
    '-orderNumber': '-order_number',
}

_ITEM_ID_PARAM = OpenApiParameter('item_id', str, OpenApiParameter.PATH, description='UUID položky objednávky (order_items.id).')
_PLACEMENT_ID_PARAM = OpenApiParameter('placement_id', str, OpenApiParameter.PATH, description='UUID umístění potisku.')


@extend_schema_view(
    list=extend_schema(
        tags=_TAG,
        summary='Seznam objednávek',
        description=(
            'Filtry: `status` (víc hodnot oddělených čárkou, např. '
            '`NEW,PAID,PROCESSING`), `paymentStatus`, `shipmentStatus`, '
            '`paymentMethod`, `carrier` (id dopravce), `customer` (id '
            'zákazníka - pro zobrazení "všechny objednávky tohoto zákazníka"), '
            '`hasCustom` (true/false - jen objednávky s/bez vlastního potisku), '
            '`createdFrom`/`createdTo` (ISO datum), `q` (fulltext: číslo '
            'objednávky, e-mail a jméno zákazníka, sledovací číslo). Řazení '
            '`ordering`: `-createdAt` (výchozí), `total`, `orderNumber` (a '
            'jejich varianty s/bez pomlčky). Řádek obsahuje `itemCount` a '
            '`hasCustom` jako anotace z databáze (ne N+1).'
        ),
    ),
    retrieve=extend_schema(
        tags=_TAG,
        summary='Detail objednávky',
        description=(
            'Kompletní detail: zákazník, obě adresy, doprava, platba, '
            'poznámka, rozpad položek (motiv/typ/střih/barva/velikost/cena) a u '
            'vlastních položek `customText` + umístění s náhledem a názvem '
            'bloku. Navíc:\n'
            '- `allowedTransitions` - do kterých stavů smí TENTO přihlášený '
            'uživatel (podle role) objednávku právě teď posunout; prázdné '
            'pole = žádný přechod není momentálně možný.\n'
            '- `isLocked` - `true` mimo stavy QUOTE_REQUESTED/NEW, kdy nejde '
            'měnit ceny, dopravu ani adresy (`trackingNumber` lze měnit '
            'kdykoli).\n'
            '- `confirmedAt` - kdy admin objednávku naposledy potvrdil '
            '(`null` = ještě nikdy, nebo byla potvrzená cena od té doby '
            'změněna a potvrzení tím zaniklo).'
        ),
    ),
)
class AdminOrderViewSet(viewsets.ModelViewSet):
    """Objednávky - jádro administrace. Nikdy se nemažou, zrušení = status CANCELLED."""
    permission_classes = [IsStaffOrAdmin]
    http_method_names = ['get', 'post', 'patch', 'put', 'delete', 'head', 'options']
    filter_backends = []  # every filter below is bespoke (comma lists, camelCase params)

    def get_queryset(self):
        queryset = (
            Order.objects.select_related('customer', 'carrier')
            .annotate(
                item_count=Count('items', distinct=True),
                has_custom=Case(When(items__is_custom=True, then=True), default=False, output_field=BooleanField()),
            )
            .distinct()
        )
        params = self.request.query_params

        status_param = params.get('status')
        if status_param:
            queryset = queryset.filter(status__in=[v.strip() for v in status_param.split(',') if v.strip()])

        for model_field, query_param in (
            ('payment_status', 'paymentStatus'),
            ('shipment_status', 'shipmentStatus'),
            ('payment_method', 'paymentMethod'),
            ('carrier_id', 'carrier'),
            ('customer_id', 'customer'),
        ):
            value = params.get(query_param)
            if value:
                queryset = queryset.filter(**{model_field: value})

        has_custom = params.get('hasCustom')
        if has_custom is not None:
            wants_custom = has_custom.lower() in ('1', 'true', 'yes')
            queryset = queryset.filter(items__is_custom=True) if wants_custom else queryset.exclude(items__is_custom=True)
            queryset = queryset.distinct()

        created_from = params.get('createdFrom')
        if created_from:
            queryset = queryset.filter(created_at__date__gte=created_from)
        created_to = params.get('createdTo')
        if created_to:
            queryset = queryset.filter(created_at__date__lte=created_to)

        search_term = params.get('q')
        if search_term:
            queryset = queryset.filter(
                Q(order_number__icontains=search_term)
                | Q(customer__email__icontains=search_term)
                | Q(customer__first_name__icontains=search_term)
                | Q(customer__last_name__icontains=search_term)
                | Q(tracking_number__icontains=search_term)
            )

        ordering = _ORDERING_MAP.get(params.get('ordering', '-createdAt'), '-created_at')
        return queryset.order_by(ordering)

    def get_serializer_class(self):
        if self.action == 'list':
            return AdminOrderListSerializer
        if self.action == 'create':
            return OrderCreateSerializer
        return AdminOrderDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action == 'create':
            context['is_admin'] = True
        return context

    def _is_admin_role(self):
        return self.request.user.role == StaffRole.ADMIN

    @extend_schema(
        tags=_TAG,
        summary='Ručně založit objednávku (telefon/e-mail)',
        description=(
            'Pro objednávky přijaté telefonicky nebo e-mailem, kdy je admin '
            'zadává za zákazníka. **Stejné tělo a validace jako veřejné `POST '
            '/orders`**, se dvěma rozdíly: `termsAccepted` není povinné a u '
            'položek navíc smíš poslat `unitPrice` rovnou (hodí se pro vlastní '
            'potisk, kde cenu domlouváš předem telefonem - bez toho zůstane '
            '`unitPrice` null jako u veřejné cesty). Zákazník se stejně jako '
            'při veřejném checkoutu dohledá/založí podle e-mailu.'
        ),
        request=OrderCreateSerializer,
        responses={201: AdminOrderDetailSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        detail = AdminOrderDetailSerializer(order, context=self.get_serializer_context())
        return Response(detail.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=_TAG,
        summary='Upravit adresy, dopravu, platební metodu, poznámku',
        description=(
            'Částečná úprava (PATCH) - pošli jen pole, která měníš. '
            '`billing`/`shipping`/`carrierId`/`deliveryType`/`pickupPointId`/'
            '`pickupPointName`/`paymentMethod`/`note` jdou měnit **jen ve '
            'stavech QUOTE_REQUESTED a NEW** - jinde vrátí 409 `ORDER_LOCKED`. '
            '`trackingNumber` je výjimka a smí se měnit kdykoli (i na '
            'odeslané/doručené objednávce, pro dodatečnou opravu). Pro ocenění '
            'položek a dopravy použij samostatné endpointy '
            '(`.../items/{itemId}`, `.../shipping-cost`), ne tento.'
        ),
        request=AdminOrderUpdateSerializer,
        responses={
            200: AdminOrderDetailSerializer,
            409: OpenApiResponse(description='ORDER_LOCKED - objednávka není ve stavu QUOTE_REQUESTED/NEW.'),
        },
    )
    def partial_update(self, request, *args, **kwargs):
        order = self.get_object()
        serializer = AdminOrderUpdateSerializer(order, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if set(data.keys()) & LOCKED_UPDATE_FIELDS:
            services.ensure_order_editable(order)

        if 'billing' in data:
            billing = data['billing']
            order.billing_full_name = billing['full_name']
            order.billing_company_name = billing.get('company_name', '')
            order.billing_ico = billing.get('ico', '')
            order.billing_dic = billing.get('dic', '')
            order.billing_street = billing['street']
            order.billing_city = billing['city']
            order.billing_zip = billing['zip']
            order.billing_country = billing.get('country', 'CZ')
        if 'shipping' in data:
            shipping = data['shipping']
            order.shipping_full_name = shipping['full_name']
            order.shipping_company_name = shipping.get('company_name', '')
            order.shipping_street = shipping['street']
            order.shipping_city = shipping['city']
            order.shipping_zip = shipping['zip']
            order.shipping_country = shipping.get('country', 'CZ')
        if 'carrier' in data:
            order.carrier = data['carrier']
        if 'delivery_type' in data:
            order.delivery_type = data['delivery_type']
        if 'pickup_point_id' in data:
            order.pickup_point_id = data['pickup_point_id']
        if 'pickup_point_name' in data:
            order.pickup_point_name = data['pickup_point_name']
        if 'payment_method' in data:
            order.payment_method = data['payment_method']
        if 'note' in data:
            order.note = data['note']
        if 'tracking_number' in data:
            order.tracking_number = data['tracking_number']  # never locked

        order.save()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @extend_schema(exclude=True)
    def update(self, request, *args, **kwargs):
        # 'put' is in http_method_names only so the shipping-cost action can
        # use PUT (per the API guide) - that enables the router's default
        # full-replace PUT on the detail route too (ModelViewSet always
        # provides `update`), which was never intended or designed for. Block
        # it explicitly instead of leaving an untested full-replace live.
        raise MethodNotAllowed('PUT')

    @extend_schema(exclude=True)
    def destroy(self, request, *args, **kwargs):
        # Same reasoning as update() above, for 'delete' (needed only for
        # the shipping-cost reset action) - orders are never hard-deleted,
        # cancellation is a status (see class docstring).
        raise MethodNotAllowed('DELETE')

    @extend_schema(
        tags=_TAG,
        summary='Ocenit položku objednávky',
        description=(
            '**Hlavní nástroj pro ocenění poptávky** (vlastní potisk čeká na '
            'cenu od admina). `unitPrice` v haléřích (pošli `null` pro vrácení '
            'položky zpět do stavu "čeká na ocenění") a/nebo `quantity` '
            '(1-500) - obě pole nepovinná, pošli jen to, co měníš. Po uložení '
            'se automaticky přepočítá `subtotal` a `total` celé objednávky a '
            'vrátí se **celá objednávka**, ne jen položka. Jen ve stavech '
            'QUOTE_REQUESTED/NEW, jinak 409 `ORDER_LOCKED`. Pokud byla '
            'objednávka už potvrzená (`confirmedAt` nebylo null), změna ceny '
            'potvrzení zruší (`confirmedAt` → null) - admin musí zavolat '
            '`/confirm` znovu, aby zákazník dostal e-mail s aktuální cenou.'
        ),
        parameters=[_ITEM_ID_PARAM],
        request=AdminOrderItemPriceSerializer,
        responses={
            200: AdminOrderDetailSerializer,
            404: OpenApiResponse(description='Položka s daným item_id v této objednávce neexistuje.'),
            409: OpenApiResponse(description='ORDER_LOCKED - objednávka není ve stavu QUOTE_REQUESTED/NEW.'),
        },
    )
    @action(detail=True, methods=['patch'], url_path=r'items/(?P<item_id>[^/.]+)')
    def update_item(self, request, pk=None, item_id=None):
        order = self.get_object()
        item = order.items.filter(id=item_id).first()
        if item is None:
            raise NotFound(_('Item not found.'))

        serializer = AdminOrderItemPriceSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        kwargs = {k: v for k, v in serializer.validated_data.items() if k in ('unit_price', 'quantity')}
        services.update_item_pricing(order, item, **kwargs)

        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=_TAG,
        summary='Upravit umístění vlastního potisku',
        description=(
            'Oprava rozměrů/pozice/rotace umístění, nebo přesun na jiný '
            'tiskový blok (`printAreaId`) - všechna pole nepovinná, pošli jen '
            'co měníš. Server znovu zkontroluje, že (případně nový) tiskový '
            'blok patří ke stejnému typu oblečení jako položka a že se '
            '(případně nové) rozměry vejdou do bloku - při porušení 422, '
            'stejně jako při `POST /orders`. Jen ve stavech '
            'QUOTE_REQUESTED/NEW, jinak 409 `ORDER_LOCKED`.'
        ),
        parameters=[_ITEM_ID_PARAM, _PLACEMENT_ID_PARAM],
        request=AdminPlacementUpdateSerializer,
        responses={
            200: AdminOrderDetailSerializer,
            404: OpenApiResponse(description='Položka nebo umístění s daným id v této objednávce neexistuje.'),
            409: OpenApiResponse(description='ORDER_LOCKED - objednávka není ve stavu QUOTE_REQUESTED/NEW.'),
            422: OpenApiResponse(description='Blok jiného typu oblečení, nebo umístění se nevejde do rozměrů bloku.'),
        },
    )
    @action(detail=True, methods=['patch'], url_path=r'items/(?P<item_id>[^/.]+)/placements/(?P<placement_id>[^/.]+)')
    def update_placement(self, request, pk=None, item_id=None, placement_id=None):
        order = self.get_object()
        services.ensure_order_editable(order)
        item = order.items.filter(id=item_id).first()
        if item is None:
            raise NotFound(_('Item not found.'))
        placement = item.design_placements.filter(id=placement_id).first()
        if placement is None:
            raise NotFound(_('Placement not found.'))

        serializer = AdminPlacementUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        print_area = data.get('print_area', placement.print_area)
        width = data.get('width', placement.width)
        height = data.get('height', placement.height)
        x = data.get('x', placement.x)
        y = data.get('y', placement.y)

        if print_area.product_type_id != item.product_type_id:
            raise services.BusinessError(
                _('The print area belongs to a different product type than the item.'),
                code='PRINT_AREA_PRODUCT_TYPE_MISMATCH',
            )
        services.validate_placement_bounds(print_area, x, y, width, height)

        for field in ('print_area', 'x', 'y', 'width', 'height', 'rotation_deg'):
            if field in data:
                setattr(placement, field, data[field])
        placement.save()

        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=_TAG,
        summary='Stáhnout originální podklad zákazníka',
        description=(
            'Stažení souboru, který zákazník nahrál pro vlastní potisk na '
            'dané umístění - pro tisk nebo kontrolu. Odpověď přesměruje na '
            'uložený soubor (`url` z `custom_design_placements`); v '
            'produkčním nastavení za privátním úložištěm by se toto chování '
            'nahradilo proxováním s `Content-Disposition: attachment`.'
        ),
        parameters=[_ITEM_ID_PARAM, _PLACEMENT_ID_PARAM],
        responses={
            302: OpenApiResponse(description='Přesměrování na uložený soubor.'),
            404: OpenApiResponse(description='Položka nebo umístění s daným id v této objednávce neexistuje.'),
        },
    )
    @action(detail=True, methods=['get'], url_path=r'items/(?P<item_id>[^/.]+)/placements/(?P<placement_id>[^/.]+)/file')
    def placement_file(self, request, pk=None, item_id=None, placement_id=None):
        from django.http import HttpResponseRedirect

        order = self.get_object()
        item = order.items.filter(id=item_id).first()
        if item is None:
            raise NotFound(_('Item not found.'))
        placement = item.design_placements.filter(id=placement_id).first()
        if placement is None:
            raise NotFound(_('Placement not found.'))
        # Local/dev storage serves files directly; swap for an attachment
        # proxy response if/when file storage moves behind a private bucket.
        return HttpResponseRedirect(placement.url)

    @extend_schema(
        methods=['PUT'],
        tags=_TAG,
        summary='Nastavit cenu dopravy',
        description=(
            '`shippingCost` v haléřích (`0` znamená dopravu zdarma, ne '
            '"nenastaveno" - rozdíl od `null`, které znamená "ještě '
            'nerozhodnuto"). Nastaví `shippingCostManual: true` - od té '
            'chvíle se cena dopravy při změně počtu kusů nepřepočítává '
            'automaticky (vrať se k automatice přes `DELETE` na stejné '
            'URL). Po uložení se přepočítá `total` celé objednávky. Jen ve '
            'stavech QUOTE_REQUESTED/NEW, jinak 409 `ORDER_LOCKED`. Stejně '
            'jako u ocenění položky, pokud byla objednávka už potvrzená, '
            'tímto se potvrzení zruší a je nutné zavolat `/confirm` znovu.'
        ),
        request=AdminShippingCostSerializer,
        responses={
            200: AdminOrderDetailSerializer,
            409: OpenApiResponse(description='ORDER_LOCKED - objednávka není ve stavu QUOTE_REQUESTED/NEW.'),
        },
    )
    @extend_schema(
        methods=['DELETE'],
        tags=_TAG,
        summary='Vrátit cenu dopravy na automatiku',
        description=(
            'Zruší `shippingCostManual` a dopočítá cenu dopravy znovu podle '
            'aktuálního dopravce, typu doručení a počtu kusů (stejně jako '
            'při vzniku objednávky) - `null`, pokud pro tuto kombinaci není '
            'nastavená cena. Jen ve stavech QUOTE_REQUESTED/NEW, jinak 409 '
            '`ORDER_LOCKED`.'
        ),
        responses={
            200: AdminOrderDetailSerializer,
            409: OpenApiResponse(description='ORDER_LOCKED - objednávka není ve stavu QUOTE_REQUESTED/NEW.'),
        },
    )
    @action(detail=True, methods=['put', 'delete'], url_path='shipping-cost')
    def shipping_cost(self, request, pk=None):
        order = self.get_object()
        if request.method == 'DELETE':
            services.reset_shipping_cost(order)
        else:
            serializer = AdminShippingCostSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            services.set_shipping_cost(order, serializer.validated_data['shipping_cost'])
        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=_TAG,
        summary='Založit zásilku u Packety',
        description=(
            'Zavolá Packeta API a založí zásilku - funguje pro výdejní místo '
            'i doručení na adresu podle `deliveryType` objednávky. `weight` '
            '(kg) je povinné, nic v systému váhu balíku nepočítá, admin ji '
            'zadá podle skutečnosti. `value` (haléře) je nepovinný override '
            'pojistné hodnoty, jinak se použije `total` objednávky. '
            'Vyžaduje nastaveného dopravce PACKETA a `deliveryType` na '
            'objednávce. Výsledné `trackingNumber` a `carrierShipmentId` se '
            'uloží na objednávku. Nejde zavolat podruhé pro stejnou '
            'objednávku (409 `SHIPMENT_ALREADY_CREATED`) - zásilka u Packety '
            'by se zdvojila.'
        ),
        request=AdminCreateShipmentSerializer,
        responses={
            200: AdminOrderDetailSerializer,
            409: OpenApiResponse(description='SHIPMENT_ALREADY_CREATED - zásilka pro tuto objednávku už existuje.'),
            502: OpenApiResponse(description='CARRIER_API_ERROR - Packeta vrátila chybu (špatné údaje, neplatné apiPassword, ...).'),
        },
    )
    @action(detail=True, methods=['post'], url_path='shipment')
    def create_shipment(self, request, pk=None):
        order = self.get_object()
        serializer = AdminCreateShipmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.create_shipment(
            order,
            weight=serializer.validated_data['weight'],
            value=serializer.validated_data.get('value'),
        )
        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=_TAG,
        summary='Stáhnout štítek zásilky',
        description=(
            'Stáhne PDF se štítkem zásilky od Packety (vyžaduje už založenou '
            'zásilku, jinak 422). Při prvním stažení posune `shipmentStatus` '
            'z CREATED na LABEL_PRINTED.'
        ),
        responses={
            200: OpenApiResponse(description='PDF se štítkem (application/pdf).'),
            422: OpenApiResponse(description='SHIPMENT_NOT_CREATED - zásilka pro tuto objednávku ještě nebyla založena.'),
            502: OpenApiResponse(description='CARRIER_API_ERROR - Packeta vrátila chybu.'),
        },
    )
    @action(detail=True, methods=['get'], url_path='shipment/label')
    def shipment_label(self, request, pk=None):
        from django.http import HttpResponse

        order = self.get_object()
        pdf_bytes = services.get_shipment_label_pdf(order)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{order.order_number}-label.pdf"'
        return response

    @extend_schema(
        tags=_TAG,
        summary='Zjistit aktuální stav zásilky u Packety',
        description=(
            'Zavolá Packeta API (`packetStatus`) a zjistí aktuální stav '
            'zásilky v jejich síti. Pokud se mapuje na náš '
            '`shipmentStatus` (CREATED/IN_TRANSIT/DELIVERED/RETURNED) a je '
            'to posun **dopředu**, uloží se - stav nikdy nejde zpátky, i '
            'kdyby Packeta vrátila zastaralá data. Vyžaduje už založenou '
            'zásilku (422 `SHIPMENT_NOT_CREATED`, pokud ne).'
        ),
        responses={
            200: AdminOrderDetailSerializer,
            422: OpenApiResponse(description='SHIPMENT_NOT_CREATED - zásilka pro tuto objednávku ještě nebyla založena.'),
            502: OpenApiResponse(description='CARRIER_API_ERROR - Packeta vrátila chybu.'),
        },
    )
    @action(detail=True, methods=['get'], url_path='shipment/status')
    def shipment_tracking(self, request, pk=None):
        order = self.get_object()
        services.sync_shipment_status(order)
        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=_TAG,
        summary='Potvrdit objednávku zákazníkovi',
        description=(
            'Odešle zákazníkovi e-mail s finální cenou a platebními '
            'instrukcemi (u platby převodem: účet, částka, variabilní '
            'symbol). Vyžaduje, aby `total` nebyl `null` (všechny položky '
            'oceněné + nastavená doprava), jinak 422 `ORDER_NOT_PRICED`. '
            'U stavu QUOTE_REQUESTED navíc přepne objednávku na NEW. '
            'Nastaví `confirmedAt` na aktuální čas - to je podmínka pro '
            'pozdější přechod NEW → PROCESSING. Nepovinné `message` je osobní '
            'text admina, který se vloží do e-mailu (např. poznámka k '
            'poptávce). **Je idempotentní** - opakované volání jen znovu '
            'pošle e-mail, nic se tím nerozbije.'
        ),
        request=AdminConfirmOrderSerializer,
        responses={
            200: AdminOrderDetailSerializer,
            422: OpenApiResponse(description='ORDER_NOT_PRICED - total je stále null.'),
        },
    )
    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        from .emails import send_order_confirmed_email

        order = self.get_object()
        message = request.data.get('message')
        with transaction.atomic():
            services.confirm_order(order)
            transaction.on_commit(lambda: send_order_confirmed_email(order, message=message))
        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=_TAG,
        summary='Změnit stav objednávky',
        description=(
            'Posune `status` podle pevné mapy povolených přechodů (viz návod, '
            'kapitola 3) - nepovolený přechod vrací 409 '
            '`INVALID_STATUS_TRANSITION`. Některé přechody navíc vyžadují '
            'splněnou podmínku (např. NEW→PAID potřebuje stanovený `total`, '
            'PROCESSING→SHIPPED potřebuje vyplněného dopravce) - při nesplnění '
            'také 409. Přechody na REFUNDED a zrušení objednávky už ve výrobě '
            '(PROCESSING→CANCELLED) smí **jen role ADMIN** (STAFF dostane '
            '403). Zjisti dopředu, co je právě povolené, z pole '
            '`allowedTransitions` v detailu objednávky. Speciální případ: '
            'cílový stav `PAID` u objednávky placené na dobírku se automaticky '
            'přesměruje na `PROCESSING` (dobírka nikdy neprochází stavem '
            'PAID). Návazné změny (např. SHIPPED nastaví `shipmentStatus` na '
            'HANDED_OVER, pokud byl nižší) se dějí automaticky. Nepovinné '
            '`note` jde k internímu záznamu.'
        ),
        request=AdminOrderStatusSerializer,
        responses={
            200: AdminOrderDetailSerializer,
            403: OpenApiResponse(description='Tento přechod smí jen role ADMIN.'),
            409: OpenApiResponse(description='INVALID_STATUS_TRANSITION - přechod není povolen nebo podmínka nesplněna.'),
        },
    )
    @action(detail=True, methods=['post'])
    def status(self, request, pk=None):
        from .emails import send_order_cancelled_email, send_order_shipped_email

        order = self.get_object()
        serializer = AdminOrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_status = serializer.validated_data['status']
        if target_status not in OrderStatus.values:
            raise serializers.ValidationError({'status': _('Unknown order status.')})

        with transaction.atomic():
            services.transition_order_status(order, target_status, is_admin=self._is_admin_role())
            if order.status == OrderStatus.SHIPPED:
                transaction.on_commit(lambda: send_order_shipped_email(order))
            elif order.status == OrderStatus.CANCELLED:
                transaction.on_commit(lambda: send_order_cancelled_email(order))

        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=_TAG,
        summary='Změnit stav platby',
        description=(
            '`paymentStatus` nezávisle na `status` objednávky - např. ruční '
            'potvrzení příchozí platby převodem, aniž by se měnil stav '
            'objednávky. Frontend může po zaplacení nabídnout i posun '
            '`status` na PAID, ale server to **sám automaticky neudělá** - '
            'jsou to dvě oddělené akce.\n\n'
            'Povolené přechody: `PENDING→PAID`, `PENDING→FAILED`, '
            '`FAILED→PENDING`, `FAILED→PAID`, `PAID→REFUNDED` - jiná '
            'kombinace 409. `PAID` a `REFUNDED` smí jen role ADMIN (STAFF '
            '403) - ručně "zaplatit" nebo vrátit platbu je riziková akce. '
            '`PAID` navíc vyžaduje stanovenou cenu (422 `ORDER_NOT_PRICED`). '
            'U zrušené nebo vrácené objednávky (status CANCELLED/REFUNDED) '
            'nejde platební stav změnit vůbec (409).'
        ),
        request=AdminPaymentStatusSerializer,
        responses={
            200: AdminOrderDetailSerializer,
            403: OpenApiResponse(description='PAID/REFUNDED smí jen role ADMIN.'),
            409: OpenApiResponse(description='Nepovolený přechod, nebo objednávka je CANCELLED/REFUNDED.'),
            422: OpenApiResponse(description='ORDER_NOT_PRICED - total je stále null.'),
        },
    )
    @action(detail=True, methods=['patch'], url_path='payment-status')
    def payment_status(self, request, pk=None):
        order = self.get_object()
        serializer = AdminPaymentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = serializer.validated_data['payment_status']
        if value not in PaymentStatus.values:
            raise serializers.ValidationError({'payment_status': _('Unknown payment status.')})
        services.transition_payment_status(order, value, is_admin=self._is_admin_role())
        order.refresh_from_db()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=_TAG,
        summary='Změnit stav zásilky',
        description=(
            '`shipmentStatus` a nepovinně `trackingNumber` najednou - typicky '
            'při vytištění štítku nebo předání dopravci, bez nutnosti měnit '
            'hlavní `status` objednávky.'
        ),
        request=AdminShipmentStatusSerializer,
        responses={200: AdminOrderDetailSerializer},
    )
    @action(detail=True, methods=['patch'], url_path='shipment-status')
    def shipment_status(self, request, pk=None):
        order = self.get_object()
        serializer = AdminShipmentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = serializer.validated_data['shipment_status']
        if value not in ShipmentStatus.values:
            raise serializers.ValidationError({'shipment_status': _('Unknown shipment status.')})
        order.shipment_status = value
        if 'tracking_number' in serializer.validated_data:
            order.tracking_number = serializer.validated_data['tracking_number']
        order.save()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=_TAG,
        summary='Znovu odeslat e-mail k objednávce',
        description=(
            'Ruční přeposlání jednoho z automatických e-mailů, pro případ že '
            'zákazník tvrdí, že mu nic nedošlo (spam, překlep v adrese po '
            'opravě...). `type` je jeden z `ORDER_RECEIVED`, `ORDER_CONFIRMED`, '
            '`ORDER_SHIPPED`, `ORDER_CANCELLED` - obsah e-mailu se vygeneruje '
            'z aktuálních dat objednávky, ne z historie.'
        ),
        request=AdminResendEmailSerializer,
        responses={204: OpenApiResponse(description='E-mail znovu odeslán.')},
    )
    @action(detail=True, methods=['post'], url_path='resend-email')
    def resend_email(self, request, pk=None):
        order = self.get_object()
        serializer = AdminResendEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .emails import resend_order_email
        resend_order_email(order, serializer.validated_data['type'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=_TAG,
        summary='Data pro výrobní list (volitelné)',
        description=(
            'Podklad pro tisknutelný výrobní list jedné objednávky - aktuálně '
            'vrací stejná data jako `GET /admin/orders/{id}`; samotné '
            'generování PDF/tisknutelného formátu je na frontendu.'
        ),
        responses={200: AdminOrderDetailSerializer},
    )
    @action(detail=True, methods=['get'], url_path='print-sheet')
    def print_sheet(self, request, pk=None):
        order = self.get_object()
        return Response(AdminOrderDetailSerializer(order, context=self.get_serializer_context()).data)
