from core.admin_lookup import LookupAdminViewSet

from .admin_serializers import AdminCarrierSerializer
from .models import Carrier


class AdminCarrierViewSet(LookupAdminViewSet):
    queryset = Carrier.objects.all()
    serializer_class = AdminCarrierSerializer
    search_fields = ['name', 'code']
