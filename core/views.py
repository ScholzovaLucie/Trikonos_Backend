from django.conf import settings
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import DeliveryType, PaymentMethod

from .emails import send_templated_email


class ConfigView(APIView):
    """GET /config - header/footer/checkout static config, no DB table."""
    permission_classes = [AllowAny]

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
    """POST /contact - sends an e-mail to the shop, stores nothing."""
    permission_classes = [AllowAny]
    throttle_scope = 'public-write'

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
