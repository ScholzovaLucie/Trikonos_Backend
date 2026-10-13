from rest_framework import serializers

from core.exceptions import EmailTakenError

from .models import StaffRole, User


class AdminUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'role', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']


class AdminUserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    name = serializers.CharField()
    role = serializers.ChoiceField(choices=StaffRole.choices, default=StaffRole.STAFF)
    password = serializers.CharField(required=False, allow_blank=False, trim_whitespace=False, default=None)

    def validate_email(self, value):
        email = value.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise EmailTakenError()
        return email


class AdminUserUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    role = serializers.ChoiceField(choices=StaffRole.choices, required=False)
    is_active = serializers.BooleanField(required=False)
