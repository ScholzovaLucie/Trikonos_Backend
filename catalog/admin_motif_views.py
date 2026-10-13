import uuid
from io import BytesIO

from django.core.files.storage import default_storage
from django.db.models import Count, ProtectedError
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


def _make_thumbnail(pil_image, max_dimension, extension):
    thumbnail = pil_image.copy()
    thumbnail.thumbnail((max_dimension, max_dimension))
    buffer = BytesIO()
    save_format = 'PNG' if extension == 'png' else 'JPEG' if extension == 'jpg' else 'WEBP'
    if save_format == 'JPEG' and thumbnail.mode in ('RGBA', 'P'):
        thumbnail = thumbnail.convert('RGB')
    thumbnail.save(buffer, format=save_format)
    return buffer.getvalue()


class AdminMotifViewSet(viewsets.ModelViewSet):
    """/admin/motifs - CRUD, reorder, and the images sub-resource (upload
    with generated thumbnails, patch altText/colorId, reorder, delete).
    """
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

    def perform_destroy(self, instance):
        images = list(instance.images or [])
        try:
            instance.delete()
        except ProtectedError:
            raise InUseError('Motiv je použitý v objednávce a nelze ho smazat - zkuste ho deaktivovat.')

        for image in images:
            for key in ('path', 'thumbnail_small_path', 'thumbnail_large_path'):
                path = image.get(key)
                if path:
                    default_storage.delete(path)

    @action(detail=True, methods=['post'])
    def images(self, request, pk=None):
        """POST /motifs/{id}/images - stores the original plus two
        thumbnails (400px/1200px), appends one addressable element to the
        images JSON array.
        """
        motif = self.get_object()
        serializer = MotifImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded = serializer.validated_data['file']
        color = serializer.validated_data.get('color')
        alt_text = serializer.validated_data.get('alt_text', '')

        extension = _PIL_FORMAT_TO_EXTENSION.get(uploaded.image.format)
        if extension is None:
            raise serializers.ValidationError({'file': 'Povolené typy jsou JPG, PNG a WEBP.'})

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
            raise serializers.ValidationError({'ids': f'Neznámá id: {", ".join(missing)}.'})

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
            raise NotFound('Obrázek nenalezen.')

        motif.images = images
        motif.save(update_fields=['images', 'updated_at'])
        return Response(AdminMotifDetailSerializer(motif).data)

    @action(detail=True, methods=['delete'], url_path=r'images/(?P<image_id>[^/.]+)')
    def delete_image(self, request, pk=None, image_id=None):
        motif = self.get_object()
        images = list(motif.images or [])
        target = next((img for img in images if img['id'] == image_id), None)
        if target is None:
            raise NotFound('Obrázek nenalezen.')

        for key in ('path', 'thumbnail_small_path', 'thumbnail_large_path'):
            path = target.get(key)
            if path:
                default_storage.delete(path)

        motif.images = [img for img in images if img['id'] != image_id]
        motif.save(update_fields=['images', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)

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
