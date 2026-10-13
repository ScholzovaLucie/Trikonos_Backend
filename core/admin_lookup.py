"""Generic admin CRUD for simple lookup tables (categories, colors, cuts,
product types, print areas, carriers) - one base ModelViewSet instead of six
near-identical ones. Subclasses set queryset/serializer_class/search_fields.
"""
from django.db.models import ProtectedError, Q
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsAdmin, IsStaffOrAdmin
from core.exceptions import InUseError
from core.pagination import AdminPagination


class ReorderSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


class LookupAdminViewSet(viewsets.ModelViewSet):
    pagination_class = AdminPagination
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    filter_backends = []
    search_fields = ['name']

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsStaffOrAdmin()]
        return [IsAdmin()]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params

        is_active = params.get('isActive')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() in ('1', 'true', 'yes'))

        search_term = params.get('q')
        if search_term:
            condition = Q()
            for field in self.search_fields:
                condition |= Q(**{f'{field}__icontains': search_term})
            queryset = queryset.filter(condition)

        return queryset.order_by('position', 'name')

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise InUseError(
                f'{instance._meta.verbose_name} je používaný a nelze ho smazat - zkuste ho deaktivovat.'
            )

    @action(detail=False, methods=['post'])
    def reorder(self, request):
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = [str(value) for value in serializer.validated_data['ids']]

        objects_by_id = {str(obj.pk): obj for obj in self.get_queryset().filter(pk__in=ids)}
        missing = [value for value in ids if value not in objects_by_id]
        if missing:
            raise serializers.ValidationError({'ids': f'Neznámá id: {", ".join(missing)}.'})

        for index, object_id in enumerate(ids):
            instance = objects_by_id[object_id]
            instance.position = (index + 1) * 10
            instance.save(update_fields=['position'])

        return Response(status=status.HTTP_204_NO_CONTENT)
