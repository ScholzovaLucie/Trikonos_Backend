from django.core import signing
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import FileTooLargeError, UnsupportedFileTypeError
from core.tokens import resolve_order_access_token, sign_file_token, sign_order_access_token
from core.uploads import (
    JPG,
    MIME_BY_KIND,
    PDF,
    PNG,
    SVG,
    design_upload_directory,
    detect_kind,
    get_image_dimensions,
    sanitize_svg,
    save_random_file,
)

from . import services
from .models import Order
from .serializers import (
    CartValidateResponseSerializer,
    CartValidateSerializer,
    DesignUploadRequestSerializer,
    DesignUploadResponseSerializer,
    OrderCreateResponseSerializer,
    OrderCreateSerializer,
    OrderCustomerDetailSerializer,
    OrderItemInputSerializer,
    OrderLookupRequestSerializer,
)


class CartValidateView(APIView):
    """POST /cart/validate - never persists anything. Each line gets its own
    verdict so the frontend can show which rows are broken before checkout.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['Checkout'],
        summary='Ověřit košík před odeslením objednávky',
        description=(
            'Pro krok "Shrnutí" v košíku a jako poslední kontrola před `POST '
            '/orders`. Tělo má **stejný tvar jako `items` v `POST /orders`** - '
            'žádné údaje o zákazníkovi, adrese ani dopravě, jen položky. '
            '**Nic se neukládá** - server jen spočítá, co by se stalo, kdyby se '
            'objednávka odeslala s těmito položkami teď.\n\n'
            'Odpověď má pro každou položku (ve stejném pořadí jako v požadavku) '
            '`valid`, `errors` (lidsky čitelné české hlášky, prázdné když je vše '
            'v pořádku), `itemLabel` (snapshot popisku, null při chybě), '
            '`unitPrice` (null u vlastního potisku nebo chybné položky), '
            '`lineTotal` (`unitPrice × quantity`, null když cena chybí). Souhrn: '
            '`subtotal` je null, pokud je kterákoli položka neplatná NEBO '
            'nedocenitelná (vlastní potisk) - tedy "null" neznamená chybu, může '
            'to jen znamenat čekání na ocenění adminem. `requiresQuote` je true, '
            'pokud košík obsahuje aspoň jednu položku s vlastním potiskem - '
            'frontend podle toho může zobrazit "cenu potvrdíme e-mailem" ještě '
            'před odeslením objednávky.\n\n'
            'Volej při každém otevření košíku (ceny/aktivita motivů se mohly '
            'mezitím změnit) a znovu těsně před `POST /orders`.'
        ),
        request=CartValidateSerializer,
        responses={200: CartValidateResponseSerializer},
    )
    def post(self, request):
        serializer = CartValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        line_results = []
        subtotal = 0
        any_unpriced = False
        requires_quote = False

        for raw_item in serializer.validated_data['items']:
            item = OrderItemInputSerializer.to_service_item(raw_item)
            result = services.validate_cart_item(item)
            line_results.append(result)

            if item['is_custom']:
                requires_quote = True
            if result['valid']:
                if result['unit_price'] is None:
                    any_unpriced = True
                else:
                    subtotal += result['unit_price'] * item['quantity']
            else:
                any_unpriced = True

        return Response({
            'items': line_results,
            'subtotal': None if any_unpriced else subtotal,
            'requires_quote': requires_quote,
        })


class OrderCreateView(generics.CreateAPIView):
    """POST /orders - public checkout, guest, no account needed."""
    serializer_class = OrderCreateSerializer
    permission_classes = [AllowAny]
    throttle_scope = 'public-write'

    @extend_schema(
        tags=['Checkout'],
        summary='Odeslat objednávku (checkout)',
        description=(
            'Finální krok checkoutu. Nevyžaduje přihlášení ani existující účet - '
            'zákazník se dohledá nebo založí podle `customer.email` (case-'
            'insensitive, uloží se malými písmeny). Server **nikdy nevěří** '
            'cenám ani popiskům poslaným z frontendu - `unitPrice` u katalogové '
            'položky se vždy spočítá z aktuální ceny motivu, `itemLabel` se '
            'vždy sestaví na serveru.\n\n'
            '**Pravidla:**\n'
            '- `termsAccepted` musí být `true`, jinak 400.\n'
            '- `shipping.sameAsBilling: true` zkopíruje fakturační adresu; jinak '
            'jsou `fullName`/`street`/`city`/`zip` doručovací adresy povinné.\n'
            '- `delivery.deliveryType: "PICKUP_POINT"` vyžaduje `pickupPointId` '
            'i `pickupPointName`.\n'
            '- Katalogová položka (`isCustom: false`) potřebuje `motifId` a '
            'žádná `placements`; vlastní položka (`isCustom: true`) nesmí mít '
            '`motifId` a potřebuje aspoň jedno umístění v `placements`, každé s '
            '`fileToken` získaným z `POST /uploads/designs` (prošlý/neplatný '
            'token → 422 `INVALID_FILE_TOKEN`).\n'
            '- Umístění musí patřit k tiskovému bloku stejného typu oblečení '
            'jako položka a vejít se do jeho rozměrů, jinak 422.\n'
            '- Prázdné `items` → 400.\n\n'
            '**Stav při vzniku:** obsahuje-li objednávka aspoň jednu vlastní '
            'položku → `QUOTE_REQUESTED` (čeká na ocenění adminem), jinak `NEW` '
            '(cena zboží je jasná, čeká se na dopravu a potvrzení). Odpověď '
            'obsahuje `accessToken` - ulož si ho na frontendu (např. do URL '
            'potvrzovací stránky), je to jediný veřejný způsob, jak se k '
            'objednávce znovu vrátit přes `GET /orders/access/{accessToken}`, '
            'kromě dohledání přes `POST /orders/lookup`. Po úspěchu se odešlou '
            'e-maily zákazníkovi i obchodu.'
        ),
        request=OrderCreateSerializer,
        responses={
            201: OrderCreateResponseSerializer,
            400: OpenApiResponse(description='Validační chyba (chybí pole, termsAccepted=false, prázdné items...).'),
            422: OpenApiResponse(description='Porušené obchodní pravidlo (neaktivní motiv, umístění mimo blok, neplatný fileToken...).'),
        },
    )
    def post(self, request, *args, **kwargs):
        # CreateAPIView dispatches POST through post() -> create(); spectacular
        # introspects the actual bound HTTP-verb method for plain (non-ViewSet)
        # generic views, not the mixin's `create`, so the schema lives here.
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()

        access_token = sign_order_access_token(order.id)
        response_serializer = OrderCreateResponseSerializer(order, context={'access_token': access_token})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class DesignUploadView(APIView):
    """POST /uploads/designs - the custom-print editor uploads here first,
    then references the returned fileToken when placing the order.
    """
    permission_classes = [AllowAny]
    throttle_scope = 'public-write'

    MAX_SIZE = 20 * 1024 * 1024
    ALLOWED_KINDS = {PNG, JPG, SVG, PDF}

    @extend_schema(
        tags=['Checkout'],
        summary='Nahrát podklad pro vlastní potisk',
        description=(
            'První krok editoru vlastního potisku - nahraje se sem obrázek '
            'PŘED odesláním objednávky. Multipart `file`, povolené PNG, JPG, '
            'SVG, PDF, max 20 MB (jinak 413). Typ souboru se ověřuje podle '
            '**skutečného obsahu** (magic bytes), ne podle přípony v názvu - '
            'podvržený soubor s jinou příponou vrátí 422 '
            '`UNSUPPORTED_FILE_TYPE`. SVG se navíc sanitizuje (odstraní se '
            '`<script>`, `on*` atributy a externí odkazy).\n\n'
            'Soubor se uloží pod náhodným názvem (nikdy pod jménem od '
            'uživatele) a vrátí se `fileToken` - **podepsaný, platný 7 dní**. '
            'Tento token (ne `url`) pošli v `placements[].fileToken` při '
            '`POST /orders` - je to jediný způsob, jak soubor napojit na '
            'objednávku. `url` slouží jen k zobrazení náhledu v editoru. '
            '`widthPx`/`heightPx` jsou k dispozici jen pro rastrové obrázky '
            '(PNG/JPG), pro SVG/PDF jsou `null`.'
        ),
        request=DesignUploadRequestSerializer,
        responses={
            201: DesignUploadResponseSerializer,
            413: OpenApiResponse(description='Soubor větší než 20 MB.'),
            422: OpenApiResponse(description='Nepodporovaný typ souboru (podle obsahu, ne přípony).'),
        },
    )
    def post(self, request):
        uploaded = request.data.get('file')
        if uploaded is None:
            raise serializers.ValidationError({'file': 'Soubor je povinný.'})
        if uploaded.size > self.MAX_SIZE:
            raise FileTooLargeError()

        data = uploaded.read()
        kind = detect_kind(data)
        if kind not in self.ALLOWED_KINDS:
            raise UnsupportedFileTypeError('Povolené typy jsou PNG, JPG, SVG a PDF.')
        if kind == SVG:
            data = sanitize_svg(data)

        width_px, height_px = get_image_dimensions(data, kind)
        _, url = save_random_file(data, design_upload_directory(), kind)
        payload = {
            'url': url,
            'mime_type': MIME_BY_KIND[kind],
            'size': len(data),
        }
        file_token = sign_file_token(payload)

        return Response({
            'file_token': file_token,
            'url': url,
            'file_name': url.rsplit('/', 1)[-1],
            'size': payload['size'],
            'mime_type': payload['mime_type'],
            'width_px': width_px,
            'height_px': height_px,
        }, status=status.HTTP_201_CREATED)


def _order_queryset():
    return Order.objects.select_related('customer', 'carrier').prefetch_related(
        'items__design_placements__print_area',
    )


class OrderLookupView(APIView):
    """POST /orders/lookup - number + e-mail. A mismatch on either field
    returns the exact same 404, so the response never reveals which part
    was wrong.
    """
    permission_classes = [AllowAny]
    throttle_scope = 'public-write'

    @extend_schema(
        tags=['Checkout'],
        summary='Dohledat objednávku (číslo + e-mail)',
        description=(
            'Pro stránku "Stav objednávky", kam se zákazník dostane bez odkazu '
            'z e-mailu - zadá číslo objednávky (`orderNumber`, např. '
            '`TRK-2026-0042`) a e-mail, který uvedl při objednání. Shoda obojího '
            '→ vrátí detail objednávky + čerstvý `accessToken` (stejný tvar jako '
            '`GET /orders/access/{accessToken}`). Neshoda v čísle NEBO v '
            'e-mailu → **vždy stejná 404** - odpověď nikdy neprozradí, jestli '
            'bylo špatně číslo, e-mail, nebo objednávka neexistuje. E-mail se '
            'posílá v těle požadavku, nikdy v URL (aby nezůstal v historii '
            'prohlížeče ani v logu serveru).'
        ),
        request=OrderLookupRequestSerializer,
        responses={
            200: OrderCustomerDetailSerializer,
            404: OpenApiResponse(description='Objednávka nenalezena (číslo i e-mail musí sedět současně).'),
        },
    )
    def post(self, request):
        serializer = OrderLookupRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = _order_queryset().filter(
            order_number=serializer.validated_data['order_number'],
            customer__email__iexact=serializer.validated_data['email'],
        ).first()
        if order is None:
            raise NotFound('Objednávka nebyla nalezena.')

        access_token = sign_order_access_token(order.id)
        response_serializer = OrderCustomerDetailSerializer(order, context={'access_token': access_token})
        return Response(response_serializer.data)


class OrderAccessView(APIView):
    """GET /orders/access/{accessToken} - the link sent in confirmation
    e-mails. Never exposed as a plain GET /orders/{id}.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['Checkout'],
        summary='Detail objednávky přes přístupový token',
        description=(
            'Pro potvrzovací stránku po odeslání objednávky a pro odkaz '
            'rozeslaný v e-mailech (přijetí, potvrzení, odeslání...). '
            '`accessToken` dostaneš z odpovědi `POST /orders` nebo `POST '
            '/orders/lookup` - je to podepsané ID objednávky s platností 180 '
            'dní, bez vlastního sloupce v databázi. Neplatný nebo prošlý token '
            'vrátí 404 (ne 401/403 - z pohledu veřejnosti objednávka "neexistuje"). '
            'Tohle je jediný veřejný způsob, jak se dostat k detailu objednávky - '
            '**holé `GET /orders/{id}` bez tokenu veřejně neexistuje.** Odpověď '
            'obsahuje i `paymentInstructions` (účet, částka, variabilní symbol) '
            'pokud je platba převodem a cena je už stanovená.'
        ),
        responses={
            200: OrderCustomerDetailSerializer,
            404: OpenApiResponse(description='Token je neplatný, podvržený nebo prošlý.'),
        },
    )
    def get(self, request, access_token):
        try:
            order_id = resolve_order_access_token(access_token)
        except signing.BadSignature:
            raise NotFound('Objednávka nebyla nalezena.')

        order = _order_queryset().filter(id=order_id).first()
        if order is None:
            raise NotFound('Objednávka nebyla nalezena.')

        response_serializer = OrderCustomerDetailSerializer(order, context={'access_token': access_token})
        return Response(response_serializer.data)
