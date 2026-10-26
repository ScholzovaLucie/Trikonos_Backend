from django.urls import path
from rest_framework.routers import DefaultRouter

from .admin_views import (
    AdminCarrierViewSet,
    AdminPacketaConfigView,
    AdminShippingPriceViewSet,
    AdminShippingQuantityDiscountViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register('carriers', AdminCarrierViewSet, basename='admin-carrier')
router.register('shipping-prices', AdminShippingPriceViewSet, basename='admin-shipping-price')
router.register(
    'shipping-quantity-discounts', AdminShippingQuantityDiscountViewSet, basename='admin-shipping-quantity-discount',
)

urlpatterns = [
    path(
        'packeta-config',
        AdminPacketaConfigView.as_view({'get': 'list', 'put': 'update'}),
        name='admin-packeta-config',
    ),
] + router.urls
