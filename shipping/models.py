from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import SimpleLookup


class Carrier(SimpleLookup):
    """Delivery carrier. No pricing/capability flags - shipping cost is
    priced manually by admin per order (orders.shipping_cost).
    """
    code = models.CharField(max_length=50, unique=True, help_text='Internal code, e.g. PACKETA')

    class Meta(SimpleLookup.Meta):
        db_table = 'carriers'
        verbose_name = _('carrier')
        verbose_name_plural = _('carriers')
