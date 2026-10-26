from rest_framework import serializers


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
