import uuid
from io import BytesIO

from django.core.files.storage import default_storage
from django.db.models import Count, ProtectedError
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from PIL import Image
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from accounts.permissions import IsAdmin, IsStaffOrAdmin
from core.admin_lookup import ReorderSerializer
from core.exceptions import InUseError
from core.pagination import AdminPagination
from core.uploads import save_random_file

from .admin_motif_serializers import (
    AdminMotifDetailSerializer,
    AdminMotifListSerializer,
    AdminMotifWriteSerializer,
    MotifImagePatchSerializer,
    MotifImageReorderSerializer,
    MotifImageUploadSerializer,
)
from .models import Motif

THUMBNAIL_MAX_DIMENSIONS = {'small': 400, 'large': 1200}
_PIL_FORMAT_TO_EXTENSION = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}

_TAG = ['Admin - motivy']


def _make_thumbnail(pil_image, max_dimension, extension):
    thumbnail = pil_image.copy()
    thumbnail.thumbnail((max_dimension, max_dimension))
    buffer = BytesIO()
    save_format = 'PNG' if extension == 'png' else 'JPEG' if extension == 'jpg' else 'WEBP'
    if save_format == 'JPEG' and thumbnail.mode in ('RGBA', 'P'):
        thumbnail = thumbnail.convert('RGB')
    thumbnail.save(buffer, format=save_format)
    return buffer.getvalue()


@extend_schema_view(
    list=extend_schema(
        tags=_TAG,
        summary='Seznam motivů (administrace)',
        description=(
            'Na rozdíl od veřejného `GET /motifs` vrací VŠECHNY motivy včetně '
            'neaktivních. Filtry: `category` (id kategorie), `isActive`, `q` '
            '(fulltext v názvu). Řazení `ordering`: `position` (výchozí), '
            '`-createdAt`, `name`. Řádek obsahuje `ordersCount` (v kolika '
            'objednávkách se motiv objevil - orientační, pro rychlý přehled '
            'popularity v administraci). Čtení smí ADMIN i STAFF.'
        ),
    ),
    retrieve=extend_schema(
        tags=_TAG,
        summary='Detail motivu (administrace)',
        description='Plný detail včetně celého pole `images` (ne jen hlavní obrázek).',
    ),
    create=extend_schema(
        tags=_TAG,
        summary='Vytvořit motiv',
        description=(
            'Jen role ADMIN. `slug` je nepovinný - když chybí, vygeneruje se z '
            '`name` (musí vyjít unikátní, jinak 400). Nový motiv vznikne bez '
            'obrázků - ty se nahrávají až následně přes `POST /motifs/{id}/images`.'
        ),
    ),
    partial_update=extend_schema(
        tags=_TAG,
        summary='Upravit motiv',
        description=(
            'Jen role ADMIN. Částečná úprava (PATCH). **Důležité:** změna `price` '
            'nijak neovlivní ceny v už existujících objednávkách - cena je v '
            '`order_items.unit_price` uložená jako snapshot v okamžiku objednání, '
            'ne odkaz na aktuální cenu motivu.'
        ),
    ),
)
class AdminMotifViewSet(viewsets.ModelViewSet):
    """Motivy v katalogu a jejich fotky."""
    pagination_class = AdminPagination
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    filter_backends = []

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsStaffOrAdmin()]
        return [IsAdmin()]

    def get_queryset(self):
        queryset = Motif.objects.select_related('category').annotate(
            orders_count=Count('order_items', distinct=True),
        )
        params = self.request.query_params

        category_id = params.get('category')
        if category_id:
            queryset = queryset.filter(category_id=category_id)

        is_active = params.get('isActive')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() in ('1', 'true', 'yes'))

        search_term = params.get('q')
        if search_term:
            queryset = queryset.filter(name__icontains=search_term)

        ordering = {'position': 'position', '-createdAt': '-created_at', 'name': 'name'}.get(
            params.get('ordering', 'position'), 'position',
        )
        return queryset.order_by(ordering, 'name')

    def get_serializer_class(self):
        if self.action == 'list':
            return AdminMotifListSerializer
        if self.action in ('create', 'partial_update', 'update'):
            return AdminMotifWriteSerializer
        return AdminMotifDetailSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        motif = serializer.save(images=[])
        return Response(AdminMotifDetailSerializer(motif).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        motif = self.get_object()
        serializer = self.get_serializer(motif, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AdminMotifDetailSerializer(motif).data)

    @extend_schema(
        tags=_TAG,
        summary='Smazat motiv',
        description=(
            'Jen role ADMIN. Pokud je motiv použitý v jakékoli objednávce, vrátí '
            '409 `IN_USE` - použij `isActive: false` místo mazání. Když mazání '
            'projde, smažou se i všechny soubory fotek (originály i miniatury) z '
            'úložiště, ne jen záznam z `images` JSON.'
        ),
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        images = list(instance.images or [])
        try:
            instance.delete()
        except ProtectedError:
            raise InUseError(_('This motif is used in an order and cannot be deleted - try deactivating it instead.'))

        for image in images:
            for key in ('path', 'thumbnail_small_path', 'thumbnail_large_path'):
                path = image.get(key)
                if path:
                    default_storage.delete(path)

    @extend_schema(
        tags=_TAG,
        summary='Nahrát fotku motivu',
        description=(
            'Multipart upload (`file` + nepovinné `altText`, `colorId`). Povolené '
            'typy JPG, PNG, WEBP, max 10 MB - typ se ověřuje podle skutečného '
            'obsahu souboru (Pillow), ne podle přípony v názvu. Server vygeneruje '
            'dvě zmenšeniny (400px a 1200px na delší straně) a uloží je společně s '
            'originálem pod náhodným názvem. Nový prvek se přidá na konec pole '
            '`images` a dostane vlastní `id` (UUID), přes které se dá dál '
            'adresovat (`PATCH`/`DELETE .../images/{imageId}`). **První obrázek v '
            'poli `images` je vždy ten hlavní** zobrazovaný ve výpisu - pořadí '
            'měníš přes `POST /motifs/{id}/images/reorder`.'
        ),
        request=MotifImageUploadSerializer,
        responses={201: AdminMotifDetailSerializer},
    )
    @action(detail=True, methods=['post'])
    def images(self, request, pk=None):
        motif = self.get_object()
        serializer = MotifImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded = serializer.validated_data['file']
        color = serializer.validated_data.get('color')
        alt_text = serializer.validated_data.get('alt_text', '')

        extension = _PIL_FORMAT_TO_EXTENSION.get(uploaded.image.format)
        if extension is None:
            raise serializers.ValidationError({'file': _('Allowed types are JPG, PNG and WEBP.')})

        uploaded.seek(0)
        original_bytes = uploaded.read()
        directory = f'motifs/{motif.id}'

        original_path, original_url = save_random_file(original_bytes, directory, extension)

        pil_image = Image.open(BytesIO(original_bytes))
        pil_image.load()
        small_bytes = _make_thumbnail(pil_image, THUMBNAIL_MAX_DIMENSIONS['small'], extension)
        small_path, small_url = save_random_file(small_bytes, directory, extension)
        large_bytes = _make_thumbnail(pil_image, THUMBNAIL_MAX_DIMENSIONS['large'], extension)
        large_path, large_url = save_random_file(large_bytes, directory, extension)

        images = list(motif.images or [])
        images.append({
            'id': str(uuid.uuid4()),
            'url': original_url,
            'path': original_path,
            'thumbnail_small_url': small_url,
            'thumbnail_small_path': small_path,
            'thumbnail_large_url': large_url,
            'thumbnail_large_path': large_path,
            'alt_text': alt_text,
            'position': len(images),
            'color_id': str(color.id) if color else None,
        })
        motif.images = images
        motif.save(update_fields=['images', 'updated_at'])
        return Response(AdminMotifDetailSerializer(motif).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=_TAG,
        summary='Změnit pořadí fotek motivu',
        description=(
            'Tělo `{"ids": ["imageId1", "imageId2", ...]}` v požadovaném pořadí - '
            'přepíše `position` všech uvedených obrázků podle pořadí v seznamu. '
            '**První id v seznamu se stává hlavním obrázkem.** Pokud pole `images` '
            'obsahuje obrázky, jejichž id v seznamu nejsou, zařadí se za uvedené, '
            've svém původním relativním pořadí (neztratí se).'
        ),
        request=MotifImageReorderSerializer,
        responses={200: AdminMotifDetailSerializer},
    )
    @action(detail=True, methods=['post'], url_path='images/reorder')
    def reorder_images(self, request, pk=None):
        """Registered before update_image/delete_image below so the literal
        'images/reorder' path is matched before the `(?P<image_id>...)`
        pattern (DRF routers keep extra-action URLs in method-definition
        order) - otherwise "reorder" would be swallowed as an image id.
        """
        motif = self.get_object()
        serializer = MotifImageReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = [str(value) for value in serializer.validated_data['ids']]

        images_by_id = {image['id']: image for image in (motif.images or [])}
        missing = [value for value in ids if value not in images_by_id]
        if missing:
            raise serializers.ValidationError({'ids': _('Unknown ids: %(ids)s.') % {'ids': ", ".join(missing)}})

        reordered = []
        for index, image_id in enumerate(ids):
            image = images_by_id[image_id]
            image['position'] = index
            reordered.append(image)
        remaining = [img for img in (motif.images or []) if img['id'] not in ids]
        for offset, image in enumerate(remaining, start=len(reordered)):
            image['position'] = offset
            reordered.append(image)

        motif.images = reordered
        motif.save(update_fields=['images', 'updated_at'])
        return Response(AdminMotifDetailSerializer(motif).data)

    @extend_schema(
        tags=_TAG,
        summary='Upravit popisek/barvu fotky',
        description=(
            'Změní `altText` a/nebo `colorId` jednoho obrázku v poli `images`, '
            'podle jeho `id` (ne indexu v poli). `colorId` musí odkazovat na '
            'existující barvu, jinak 400. Pro změnu pořadí nebo samotného '
            'souboru použij `reorder`/nový `POST .../images`, tento endpoint '
            'mění jen metadata.'
        ),
        parameters=[OpenApiParameter('image_id', str, OpenApiParameter.PATH, description='UUID obrázku uvnitř pole images.')],
        responses={200: AdminMotifDetailSerializer},
    )
    @action(detail=True, methods=['patch'], url_path=r'images/(?P<image_id>[^/.]+)')
    def update_image(self, request, pk=None, image_id=None):
        motif = self.get_object()
        serializer = MotifImagePatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        images = list(motif.images or [])
        for image in images:
            if image['id'] == image_id:
                if 'alt_text' in data:
                    image['alt_text'] = data['alt_text']
                if 'color' in data:
                    image['color_id'] = str(data['color'].id) if data['color'] else None
                break
        else:
            raise NotFound(_('Image not found.'))

        motif.images = images
        motif.save(update_fields=['images', 'updated_at'])
        return Response(AdminMotifDetailSerializer(motif).data)

    @extend_schema(
        tags=_TAG,
        summary='Smazat fotku motivu',
        description=(
            'Odebere obrázek z pole `images` podle jeho `id` a smaže i jeho '
            'soubory z úložiště (originál i obě miniatury) - není to jen smazání '
            'záznamu, soubor opravdu zmizí.'
        ),
        parameters=[OpenApiParameter('image_id', str, OpenApiParameter.PATH, description='UUID obrázku uvnitř pole images.')],
        responses={204: OpenApiResponse(description='Obrázek a jeho soubory smazány.')},
    )
    @action(detail=True, methods=['delete'], url_path=r'images/(?P<image_id>[^/.]+)')
    def delete_image(self, request, pk=None, image_id=None):
        motif = self.get_object()
        images = list(motif.images or [])
        target = next((img for img in images if img['id'] == image_id), None)
        if target is None:
            raise NotFound(_('Image not found.'))

        for key in ('path', 'thumbnail_small_path', 'thumbnail_large_path'):
            path = target.get(key)
            if path:
                default_storage.delete(path)

        motif.images = [img for img in images if img['id'] != image_id]
        motif.save(update_fields=['images', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=_TAG,
        summary='Změnit pořadí motivů',
        description=(
            'Tělo `{"ids": ["uuid1", "uuid2", ...]}` v požadovaném pořadí - server '
            'přepíše `position` na 10, 20, 30... podle pořadí v seznamu. Řídí '
            'výchozí pořadí ve veřejném výpisu `GET /motifs?ordering=position`.'
        ),
        request=ReorderSerializer,
        responses={204: OpenApiResponse(description='Pořadí uloženo.')},
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
