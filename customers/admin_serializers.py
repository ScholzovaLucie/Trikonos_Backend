from rest_framework import serializers

from .models import Customer


class AdminCustomerListSerializer(serializers.ModelSerializer):
    orders_count = serializers.IntegerField(read_only=True)
    total_spent = serializers.IntegerField(read_only=True, allow_null=True)
    last_order_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = Customer
        fields = [
            'id', 'email', 'phone', 'first_name', 'last_name', 'company_name',
            'marketing_consent', 'created_at', 'orders_count', 'total_spent', 'last_order_at',
        ]


class AdminCustomerOrderSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    order_number = serializers.CharField()
    status = serializers.CharField()
    total = serializers.IntegerField(allow_null=True)
    currency = serializers.CharField()
    created_at = serializers.DateTimeField()


class AdminCustomerDetailSerializer(AdminCustomerListSerializer):
    note = serializers.CharField()
    orders = serializers.SerializerMethodField()

    class Meta(AdminCustomerListSerializer.Meta):
        fields = AdminCustomerListSerializer.Meta.fields + ['note', 'orders']

    def get_orders(self, obj):
        return AdminCustomerOrderSummarySerializer(obj.orders.order_by('-created_at'), many=True).data


class AdminCustomerUpdateSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)
    phone = serializers.CharField(required=False, allow_blank=True)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    company_name = serializers.CharField(required=False, allow_blank=True)
    marketing_consent = serializers.BooleanField(required=False)
    note = serializers.CharField(required=False, allow_blank=True)
