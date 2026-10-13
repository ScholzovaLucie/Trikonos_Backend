from core.admin_lookup import LookupAdminViewSet, lookup_schema_view

from .admin_serializers import AdminCarrierSerializer
from .models import Carrier


@lookup_schema_view(
    'dopravce', 'dopravců',
    extra_note=(
        '`code` se uloží velkými písmeny (interní kód pro integrační vrstvu, '
        'např. PACKETA). Dopravci nemají v DB ceník - cenu dopravy nastavuje '
        'admin ručně u každé objednávky přes `PUT /admin/orders/{id}/shipping-cost`.'
    ),
)
class AdminCarrierViewSet(LookupAdminViewSet):
    """Dopravci (Zásilkovna, DPD, ...), nabízení zákazníkovi při checkoutu."""
    queryset = Carrier.objects.all()
    serializer_class = AdminCarrierSerializer
    search_fields = ['name', 'code']
