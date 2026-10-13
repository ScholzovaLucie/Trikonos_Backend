from django.utils.translation import gettext_lazy as _

from core.models import SimpleLookup


class Cut(SimpleLookup):
    """Men's, Women's, Unisex, Hoodie, ..."""

    class Meta(SimpleLookup.Meta):
        db_table = 'cuts'
        verbose_name = _('cut')
        verbose_name_plural = _('cuts')
