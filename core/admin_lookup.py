"""Generic admin CRUD for simple lookup tables (categories, colors, cuts,
product types, print areas, carriers) - one base ModelViewSet instead of six
near-identical ones. Subclasses set queryset/serializer_class/search_fields.
"""
from django.db.models import ProtectedError, Q
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import IsAdmin, IsStaffOrAdmin
from core.exceptions import InUseError
from core.pagination import AdminPagination


class ReorderSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


def lookup_schema_view(entity, entity_plural, extra_note='', tag='Admin - číselníky'):
    """Czech @extend_schema_view shared by every LookupAdminViewSet subclass -
    list/retrieve/create/partial_update/destroy/reorder differ only by the
    entity's name, not by behaviour, so this is parametrized once instead of
    repeated for each lookup table. `tag` defaults to the generic catalog
    taxonomy group but can be overridden (e.g. shipping's carrier/price
    lookups use 'Admin - doprava' instead) so Swagger groups them with
    their actual domain rather than dumping everything into one bucket.
    """
    note_suffix = f' {extra_note}' if extra_note else ''
    return extend_schema_view(
        list=extend_schema(
            tags=[tag],
            summary=f'Seznam {entity_plural}',
            description=(
                f'Vrátí VŠECHNY {entity_plural} včetně neaktivních (na rozdíl od '
                'veřejného API, kde se neaktivní nikdy neukážou). Filtr `isActive` '
                '(true/false) a fulltext `q` v názvu. Čtení smí ADMIN i STAFF.'
            ),
        ),
        retrieve=extend_schema(
            tags=[tag],
            summary=f'Detail {entity}',
            description=f'Detail jedné položky z {entity_plural} podle `id`.',
        ),
        create=extend_schema(
            tags=[tag],
            summary=f'Vytvořit {entity}',
            description=f'Jen role ADMIN.{note_suffix}',
        ),
        partial_update=extend_schema(
            tags=[tag],
            summary=f'Upravit {entity}',
            description=f'Jen role ADMIN. Částečná úprava (PATCH) - pošli jen pole, která měníš.{note_suffix}',
        ),
        destroy=extend_schema(
            tags=[tag],
            summary=f'Smazat {entity}',
            description=(
                f'Jen role ADMIN. Pokud je {entity} použitý v objednávce nebo motivu '
                '(DB vazba `PROTECT`), vrátí 409 `IN_USE` - použij `isActive: false` '
                'místo mazání.'
            ),
        ),
        reorder=extend_schema(
            tags=[tag],
            summary=f'Změnit pořadí {entity_plural}',
            description=(
                'Tělo `{"ids": ["uuid1", "uuid2", ...]}` v požadovaném pořadí - server '
                'přepíše `position` na 10, 20, 30... podle pořadí v seznamu (mezery '
                'po 10 nechávají místo na ruční doladění přímo v DB, kdyby bylo '
                'potřeba). Všechna id musí existovat, jinak 400.'
            ),
            request=ReorderSerializer,
            responses={204: OpenApiResponse(description='Pořadí uloženo.')},
        ),
    )


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
                _('This record (%(name)s) is in use and cannot be deleted - try deactivating it instead.')
                % {'name': instance._meta.verbose_name}
            )

    @action(detail=False, methods=['post'])
    def reorder(self, request):
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = [str(value) for value in serializer.validated_data['ids']]

        objects_by_id = {str(obj.pk): obj for obj in self.get_queryset().filter(pk__in=ids)}
        missing = [value for value in ids if value not in objects_by_id]
        if missing:
            raise serializers.ValidationError({'ids': _('Unknown ids: %(ids)s.') % {'ids': ", ".join(missing)}})

        for index, object_id in enumerate(ids):
            instance = objects_by_id[object_id]
            instance.position = (index + 1) * 10
            instance.save(update_fields=['position'])

        return Response(status=status.HTTP_204_NO_CONTENT)
