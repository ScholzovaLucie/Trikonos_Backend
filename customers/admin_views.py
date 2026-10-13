import csv

from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponse
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


class AdminCustomerViewSet(viewsets.ModelViewSet):
    """/admin/customers - read + limited edit, no delete (orders reference
    customers via PROTECT; GDPR erasure is a manual ADMIN action, out of
    scope here per the API guide).
    """
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
    """GET /customers/export-consents - ADMIN only, CSV of opted-in e-mails."""
    permission_classes = [IsAdmin]

    def get(self, request):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="marketing-consents.csv"'
        writer = csv.writer(response)
        writer.writerow(['email'])
        emails = Customer.objects.filter(marketing_consent=True).order_by('email').values_list('email', flat=True)
        for email in emails:
            writer.writerow([email])
        return response
