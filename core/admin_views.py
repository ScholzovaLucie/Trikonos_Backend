from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffOrAdmin
from customers.models import Customer
from orders.models import Order

from .admin_serializers import AdminSearchResponseSerializer


class AdminSearchView(APIView):
    """Globální vyhledávání v administraci napříč objednávkami, zákazníky a motivy."""
    permission_classes = [IsStaffOrAdmin]
    LIMIT = 5

    @extend_schema(
        tags=['Admin - přehled'],
        summary='Globální hledání (objednávky, zákazníci, motivy)',
        description=(
            'Určeno pro vyhledávací pole v horní liště administrace - jedno pole, tři '
            'typy výsledků najednou. Prohledává: objednávky (číslo objednávky, e-mail '
            'a jméno zákazníka, sledovací číslo), zákazníky (e-mail, jméno, firma, '
            'telefon) a motivy (název). Hledání je `icontains` (nerozlišuje velikost '
            'písmen, hledá podřetězec, ne jen přesnou shodu). Vrací max 5 výsledků '
            '**na každý typ** - není to stránkovaný seznam, je to rychlý náhled; pro '
            'kompletní seznam použij `GET /admin/orders`, `/admin/customers` nebo '
            '`/admin/motifs` s parametrem `q`. Bez parametru `q` (nebo prázdný) vrátí '
            'tři prázdná pole, ne chybu.'
        ),
        parameters=[OpenApiParameter('q', str, OpenApiParameter.QUERY, description='Hledaný text.')],
        responses={200: AdminSearchResponseSerializer},
    )
    def get(self, request):
        from catalog.models import Motif

        query = request.query_params.get('q', '').strip()
        if not query:
            return Response({'orders': [], 'customers': [], 'motifs': []})

        orders = Order.objects.select_related('customer').filter(
            Q(order_number__icontains=query)
            | Q(customer__email__icontains=query)
            | Q(customer__first_name__icontains=query)
            | Q(customer__last_name__icontains=query)
            | Q(tracking_number__icontains=query)
        )[:self.LIMIT]

        customers = Customer.objects.filter(
            Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(company_name__icontains=query)
            | Q(phone__icontains=query)
        )[:self.LIMIT]

        motifs = Motif.objects.filter(name__icontains=query)[:self.LIMIT]

        return Response({
            'orders': [
                {
                    'id': order.id, 'order_number': order.order_number,
                    'customer_email': order.customer.email, 'status': order.status,
                }
                for order in orders
            ],
            'customers': [
                {
                    'id': customer.id, 'email': customer.email,
                    'name': f'{customer.first_name} {customer.last_name}'.strip(),
                    'company_name': customer.company_name,
                }
                for customer in customers
            ],
            'motifs': [{'id': motif.id, 'name': motif.name, 'slug': motif.slug} for motif in motifs],
        })
