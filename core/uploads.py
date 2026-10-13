"""Shared file-upload helpers for customer design uploads (orders app) and
motif photos (catalog admin, task 9) - content sniffing by magic bytes (never
trust the extension), SVG sanitising, and random-name storage.
"""
import re
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

PNG = 'png'
JPG = 'jpg'
SVG = 'svg'
PDF = 'pdf'
WEBP = 'webp'

MIME_BY_KIND = {
    PNG: 'image/png',
    JPG: 'image/jpeg',
    SVG: 'image/svg+xml',
    PDF: 'application/pdf',
    WEBP: 'image/webp',
}

_SVG_SNIFF_WINDOW = 1024


def detect_kind(data):
    """Identify a file by its content, not its extension/declared MIME
    type - an uploaded "design.png" that isn't really a PNG must be rejected.
    """
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return PNG
    if data.startswith(b'\xff\xd8\xff'):
        return JPG
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return WEBP
    if data.startswith(b'%PDF'):
        return PDF
    head = data[:_SVG_SNIFF_WINDOW].lstrip()
    if head.startswith(b'<?xml') or head.startswith(b'<svg'):
        if b'<svg' in data[:_SVG_SNIFF_WINDOW].lower():
            return SVG
    return None


_SCRIPT_TAG_RE = re.compile(rb'<script\b.*?</script\s*>', re.IGNORECASE | re.DOTALL)
_ON_ATTR_RE = re.compile(rb'\s+on[a-z]+\s*=\s*(".*?"|\'.*?\')', re.IGNORECASE | re.DOTALL)
_EXTERNAL_HREF_RE = re.compile(
    rb'((?:xlink:)?href\s*=\s*)(["\'])\s*(https?:)[^"\']*(\2)', re.IGNORECASE,
)


def sanitize_svg(data):
    """Best-effort strip of <script>, on* event handlers and external
    http(s) references - SVGs are customer-uploaded and get rendered back
    in the admin, so they're treated the same as any other untrusted HTML.
    """
    data = _SCRIPT_TAG_RE.sub(b'', data)
    data = _ON_ATTR_RE.sub(b'', data)
    data = _EXTERNAL_HREF_RE.sub(rb'\1\2#blocked\2', data)
    return data


def get_image_dimensions(data, kind):
    if kind not in (PNG, JPG, WEBP):
        return None, None
    try:
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(data)) as image:
            return image.width, image.height
    except Exception:
        return None, None


def save_random_file(data, directory, kind):
    """Stores `data` under `directory` with a random UUID name - uploaded
    file names are never trusted/kept, only the content-sniffed extension is.
    """
    extension = kind
    name = f'{directory}/{uuid.uuid4().hex}.{extension}'
    path = default_storage.save(name, ContentFile(data))
    return path, default_storage.url(path)


def design_upload_directory():
    now = timezone.now()
    return f'designs/{now.year:04d}/{now.month:02d}'
