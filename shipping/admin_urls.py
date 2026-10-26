from django.urls import path
from rest_framework.routers import DefaultRouter

from .admin_views import AdminCarrierViewSet, AdminShippingPriceView, AdminShippingQuantityDiscountViewSet

router = DefaultRouter(trailing_slash=False)
router.register('carriers', AdminCarrierViewSet, basename='admin-carrier')
router.register(
    'shipping-quantity-discounts', AdminShippingQuantityDiscountViewSet, basename='admin-shipping-quantity-discount',
)

urlpatterns = [
    path(
        'shipping-price',
        AdminShippingPriceView.as_view({'get': 'list', 'put': 'update'}),
        name='admin-shipping-price',
    ),
] + router.urls
