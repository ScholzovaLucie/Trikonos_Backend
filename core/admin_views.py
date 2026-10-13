from datetime import timedelta

from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsStaffOrAdmin
from customers.models import Customer
from orders.models import Order, OrderStatus, PaymentMethod, PaymentStatus


class AdminDashboardView(APIView):
    """GET /dashboard - the admin landing page cards."""
    permission_classes = [IsStaffOrAdmin]

    REVENUE_EXCLUDED_STATUSES = [OrderStatus.CANCELLED, OrderStatus.REFUNDED]
    PENDING_STATUSES = [OrderStatus.NEW, OrderStatus.PAID, OrderStatus.PROCESSING]
    RECENT_ORDERS_LIMIT = 10

    def get(self, request):
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        week_ago = now - timedelta(days=7)

        orders_today = Order.objects.filter(created_at__gte=today_start).count()
        orders_yesterday = Order.objects.filter(
            created_at__gte=yesterday_start, created_at__lt=today_start,
        ).count()

        revenue_today = Order.objects.filter(created_at__gte=today_start).exclude(
            status__in=self.REVENUE_EXCLUDED_STATUSES,
        ).aggregate(total=Sum('total'))['total'] or 0

        revenue_yesterday = Order.objects.filter(
            created_at__gte=yesterday_start, created_at__lt=today_start,
        ).exclude(status__in=self.REVENUE_EXCLUDED_STATUSES).aggregate(total=Sum('total'))['total'] or 0

        new_customers_7d = Customer.objects.filter(created_at__gte=week_ago).count()
        pending_quotes = Order.objects.filter(status=OrderStatus.QUOTE_REQUESTED).count()
        orders_to_process = Order.objects.filter(status__in=self.PENDING_STATUSES).count()
        unpaid_transfers_invoices = Order.objects.filter(
            payment_method__in=[PaymentMethod.BANK_TRANSFER, PaymentMethod.INVOICE],
            payment_status=PaymentStatus.PENDING,
        ).count()

        recent_orders = Order.objects.select_related('customer').order_by('-created_at')[:self.RECENT_ORDERS_LIMIT]

        return Response({
            'orders_today': orders_today,
            'orders_yesterday': orders_yesterday,
            'revenue_today': revenue_today,
            'revenue_yesterday': revenue_yesterday,
            'new_customers_7d': new_customers_7d,
            'pending_quotes': pending_quotes,
            'orders_to_process': orders_to_process,
            'unpaid_transfers_invoices': unpaid_transfers_invoices,
            'recent_orders': [
                {
                    'order_number': order.order_number,
                    'customer_email': order.customer.email,
                    'created_at': order.created_at,
                    'total': order.total,
                    'status': order.status,
                }
                for order in recent_orders
            ],
        })


class AdminSearchView(APIView):
    """GET /search?q= - global admin search across orders/customers/motifs,
    max 5 results per type.
    """
    permission_classes = [IsStaffOrAdmin]
    LIMIT = 5

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
