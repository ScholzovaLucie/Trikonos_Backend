from rest_framework import serializers

from .models import Carrier


class AdminCarrierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Carrier
        fields = ['id', 'name', 'code', 'position', 'is_active']

    def validate_code(self, value):
        return value.strip().upper()
