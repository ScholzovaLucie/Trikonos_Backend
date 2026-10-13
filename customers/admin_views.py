import csv

from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import exceptions as drf_exceptions
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import StaffRole
from accounts.permissions import IsAdmin, IsStaffOrAdmin
from core.exceptions import EmailTakenError
from core.pagination import AdminPagination

from .admin_serializers import (
    AdminCustomerDetailSerializer,
    AdminCustomerListSerializer,
    AdminCustomerUpdateSerializer,
)
from .models import Customer

_ORDERING_MAP = {
    '-createdAt': '-created_at',
    'createdAt': 'created_at',
    '-ordersCount': '-orders_count',
    'ordersCount': 'orders_count',
    '-totalSpent': '-total_spent',
    'totalSpent': 'total_spent',
}


_TAG = ['Admin - zákazníci']


@extend_schema_view(
    list=extend_schema(
        tags=_TAG,
        summary='Seznam zákazníků',
        description=(
            'Fulltext `q` hledá v e-mailu, jménu, firmě a telefonu. Filtr '
            '`marketingConsent` (true/false). Řazení `ordering`: `-createdAt` '
            '(výchozí), `-ordersCount`, `-totalSpent` (a jejich vzestupné '
            'varianty bez pomlčky). `ordersCount`, `totalSpent` (součet `total` '
            'ze VŠECH objednávek zákazníka, i zrušených) a `lastOrderAt` jsou '
            'počítané v databázi (anotace v querysetu) - žádné N+1 dotazy ani '
            'při velkém seznamu.'
        ),
        parameters=[
            OpenApiParameter('q', str, OpenApiParameter.QUERY, description='Fulltext: e-mail, jméno, firma, telefon.'),
            OpenApiParameter('marketingConsent', bool, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('ordering', str, OpenApiParameter.QUERY, description='-createdAt, -ordersCount, -totalSpent (nebo bez pomlčky pro vzestupné).'),
        ],
    ),
    retrieve=extend_schema(
        tags=_TAG,
        summary='Detail zákazníka',
        description='Kontakt, interní poznámka, souhlas s marketingem, souhrny a seznam objednávek zákazníka.',
    ),
    partial_update=extend_schema(
        tags=_TAG,
        summary='Upravit zákazníka',
        description=(
            'Částečná úprava (PATCH): `phone`, `firstName`, `lastName`, '
            '`companyName`, `marketingConsent`, `note` smí ADMIN i STAFF. Změna '
            '`email` je navíc **jen pro ADMIN** (jinak 403) a kontroluje '
            'unikátnost - pokud už e-mail patří jinému zákazníkovi, vrátí 409 '
            '`EMAIL_TAKEN`.'
        ),
    ),
)
class AdminCustomerViewSet(viewsets.ModelViewSet):
    """Zákazníci - evidence pro administraci, bez hesla/účtu."""
    permission_classes = [IsStaffOrAdmin]
    pagination_class = AdminPagination
    http_method_names = ['get', 'patch', 'head', 'options']
    filter_backends = []

    def get_queryset(self):
        queryset = Customer.objects.annotate(
            orders_count=Count('orders', distinct=True),
            total_spent=Sum('orders__total'),
            last_order_at=Max('orders__created_at'),
        )
        params = self.request.query_params

        search_term = params.get('q')
        if search_term:
            queryset = queryset.filter(
                Q(email__icontains=search_term)
                | Q(first_name__icontains=search_term)
                | Q(last_name__icontains=search_term)
                | Q(company_name__icontains=search_term)
                | Q(phone__icontains=search_term)
            )

        marketing_consent = params.get('marketingConsent')
        if marketing_consent is not None:
            queryset = queryset.filter(marketing_consent=marketing_consent.lower() in ('1', 'true', 'yes'))

        ordering = _ORDERING_MAP.get(params.get('ordering', '-createdAt'), '-created_at')
        return queryset.order_by(ordering)

    def get_serializer_class(self):
        if self.action == 'list':
            return AdminCustomerListSerializer
        return AdminCustomerDetailSerializer

    def partial_update(self, request, *args, **kwargs):
        customer = self.get_object()
        serializer = AdminCustomerUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if 'email' in data:
            if request.user.role != StaffRole.ADMIN:
                raise drf_exceptions.PermissionDenied('Změnu e-mailu může provést jen role ADMIN.')
            new_email = data['email'].strip().lower()
            if Customer.objects.exclude(pk=customer.pk).filter(email__iexact=new_email).exists():
                raise EmailTakenError()
            customer.email = new_email

        for field in ('phone', 'first_name', 'last_name', 'company_name', 'marketing_consent', 'note'):
            if field in data:
                setattr(customer, field, data[field])

        customer.save()
        detail = self.get_queryset().get(pk=customer.pk)
        return Response(AdminCustomerDetailSerializer(detail).data)


class CustomerExportConsentsView(APIView):
    """Export e-mailů zákazníků se souhlasem s marketingem."""
    permission_classes = [IsAdmin]

    @extend_schema(
        tags=_TAG,
        summary='Export e-mailů se souhlasem s marketingem (CSV)',
        description=(
            'Jen role ADMIN. Vrátí CSV soubor (ne JSON) s jedním sloupcem `email` - '
            'jen zákazníci s `marketingConsent: true`. Odpověď má '
            '`Content-Disposition: attachment`, takže prohlížeč nabídne stažení '
            'souboru `marketing-consents.csv` místo zobrazení obsahu.'
        ),
        responses={200: OpenApiResponse(description='CSV soubor, Content-Type: text/csv.')},
    )
    def get(self, request):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="marketing-consents.csv"'
        writer = csv.writer(response)
        writer.writerow(['email'])
        emails = Customer.objects.filter(marketing_consent=True).order_by('email').values_list('email', flat=True)
        for email in emails:
            writer.writerow([email])
        return response
