from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import DeliveryType, PaymentMethod

from .emails import send_templated_email


class ChoiceOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class ConfigResponseSerializer(serializers.Serializer):
    shop_name = serializers.CharField()
    contact_email = serializers.CharField()
    contact_phone = serializers.CharField()
    instagram_url = serializers.CharField()
    bank_account = serializers.CharField()
    topbar_text = serializers.CharField()
    payment_methods = ChoiceOptionSerializer(many=True)
    delivery_types = ChoiceOptionSerializer(many=True)


class ConfigView(APIView):
    """Statická konfigurace obchodu, bez vlastní DB tabulky."""
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['Konfigurace'],
        summary='Konfigurace obchodu pro hlavičku, patičku a checkout',
        description=(
            'Vrací statické texty a hodnoty, které frontend potřebuje na víc místech '
            'a nemění se podle uživatele: text horní lišty, kontaktní e-mail a telefon, '
            'odkaz na Instagram, číslo bankovního účtu (pro zobrazení platebních '
            'instrukcí) a číselníky `paymentMethods`/`deliveryTypes` s českými popisky '
            '(`value` = hodnota, kterou posíláš v `POST /orders`, `label` = text pro '
            'uživatele). Volej jednou při startu appky, výsledek je bezpečné cachovat - '
            'hodnoty se mění jen ručně přes env proměnné na backendu.'
        ),
        responses={200: ConfigResponseSerializer},
    )
    def get(self, request):
        shop = settings.SHOP_CONFIG
        return Response({
            'shop_name': shop['NAME'],
            'contact_email': shop['CONTACT_EMAIL'],
            'contact_phone': shop['CONTACT_PHONE'],
            'instagram_url': shop['INSTAGRAM_URL'],
            'bank_account': shop['BANK_ACCOUNT'],
            'topbar_text': shop['TOPBAR_TEXT'],
            'payment_methods': [
                {'value': value, 'label': label} for value, label in PaymentMethod.choices
            ],
            'delivery_types': [
                {'value': value, 'label': label} for value, label in DeliveryType.choices
            ],
        })


class ContactSerializer(serializers.Serializer):
    name = serializers.CharField()
    email = serializers.EmailField()
    phone = serializers.CharField(required=False, allow_blank=True, default='')
    company = serializers.CharField(required=False, allow_blank=True, default='')
    message = serializers.CharField()
    # Honeypot - hidden on the real form, bots tend to fill every field.
    website = serializers.CharField(required=False, allow_blank=True, default='')


class ContactView(APIView):
    """Kontaktní formulář - jen pošle e-mail, nic neukládá."""
    permission_classes = [AllowAny]
    throttle_scope = 'public-write'

    @extend_schema(
        tags=['Konfigurace'],
        summary='Odeslat kontaktní/poptávkový formulář',
        description=(
            'Pro stránku "Kontakt" nebo "Poptat tým" bez průchodu editorem vlastního '
            'potisku. Nic se neukládá do databáze - endpoint jen pošle e-mail na adresu '
            'obchodu (`SHOP_NOTIFY_EMAIL`). Pole `website` je honeypot proti botům: '
            'na frontendu ho schovej (např. `display:none`) a nech prázdné - člověk ho '
            'nevyplní, bot často ano. Pokud je vyplněné, endpoint tiše vrátí 204 a nic '
            'neodešle (bot se to nedozví). Vždy vrací 204, i při honeypotu, aby útočník '
            'nerozlišil úspěch od detekce.'
        ),
        request=ContactSerializer,
        responses={204: OpenApiResponse(description='Zpráva odeslána (nebo honeypot tiše ignorován).')},
    )
    def post(self, request):
        serializer = ContactSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data['website']:
            return Response(status=status.HTTP_204_NO_CONTENT)

        notify_email = settings.SHOP_CONFIG['NOTIFY_EMAIL']
        if notify_email:
            send_templated_email(
                'contact_message',
                {
                    'name': data['name'],
                    'email': data['email'],
                    'phone': data.get('phone', ''),
                    'company': data.get('company', ''),
                    'message': data['message'],
                },
                subject=f'Zpráva z kontaktního formuláře - {data["name"]}',
                recipient_list=[notify_email],
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
