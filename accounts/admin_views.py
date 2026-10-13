from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from core.exceptions import LastAdminError
from core.pagination import AdminPagination

from .admin_serializers import AdminUserCreateSerializer, AdminUserSerializer, AdminUserUpdateSerializer
from .emails import send_password_setup_email
from .models import StaffRole, User
from .permissions import IsAdmin


def _has_other_active_admin(exclude):
    return User.objects.filter(role=StaffRole.ADMIN, is_active=True).exclude(pk=exclude.pk).exists()


class AdminUserViewSet(viewsets.ModelViewSet):
    """/admin/users - ADMIN only, per the permission table (STAFF has no
    access to user management at all).
    """
    queryset = User.objects.all().order_by('email')
    permission_classes = [IsAdmin]
    pagination_class = AdminPagination
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    filter_backends = []

    def get_serializer_class(self):
        if self.action == 'create':
            return AdminUserCreateSerializer
        if self.action == 'partial_update':
            return AdminUserUpdateSerializer
        return AdminUserSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        password = data.get('password')
        user = User(email=data['email'], name=data['name'], role=data['role'])
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()

        if not password:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            send_password_setup_email(user, uid, token)

        return Response(AdminUserSerializer(user).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        user = self.get_object()
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        demoting = 'role' in data and data['role'] != StaffRole.ADMIN and user.role == StaffRole.ADMIN
        deactivating = 'is_active' in data and data['is_active'] is False and user.role == StaffRole.ADMIN
        if (demoting or deactivating) and not _has_other_active_admin(exclude=user):
            raise LastAdminError()

        for field in ('name', 'role', 'is_active'):
            if field in data:
                setattr(user, field, data[field])
        user.save()
        return Response(AdminUserSerializer(user).data)

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user.pk == request.user.pk:
            raise PermissionDenied('Nemůžete smazat svůj vlastní účet.')
        if user.role == StaffRole.ADMIN and not _has_other_active_admin(exclude=user):
            raise LastAdminError()
        return super().destroy(request, *args, **kwargs)
