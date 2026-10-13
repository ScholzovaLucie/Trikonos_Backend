from rest_framework import serializers


class AdminDashboardRecentOrderSerializer(serializers.Serializer):
    order_number = serializers.CharField()
    customer_email = serializers.EmailField()
    created_at = serializers.DateTimeField()
    total = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()


class AdminDashboardResponseSerializer(serializers.Serializer):
    orders_today = serializers.IntegerField()
    orders_yesterday = serializers.IntegerField()
    revenue_today = serializers.IntegerField()
    revenue_yesterday = serializers.IntegerField()
    new_customers_7d = serializers.IntegerField()
    pending_quotes = serializers.IntegerField()
    orders_to_process = serializers.IntegerField()
    unpaid_transfers_invoices = serializers.IntegerField()
    recent_orders = AdminDashboardRecentOrderSerializer(many=True)


class AdminSearchOrderResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    order_number = serializers.CharField()
    customer_email = serializers.EmailField()
    status = serializers.CharField()


class AdminSearchCustomerResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    name = serializers.CharField()
    company_name = serializers.CharField()


class AdminSearchMotifResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()


class AdminSearchResponseSerializer(serializers.Serializer):
    orders = AdminSearchOrderResultSerializer(many=True)
    customers = AdminSearchCustomerResultSerializer(many=True)
    motifs = AdminSearchMotifResultSerializer(many=True)
