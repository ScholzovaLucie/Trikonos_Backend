from django.urls import path
from rest_framework.routers import DefaultRouter

from .admin_production_views import ProductionPlacementsView, ProductionSummaryView
from .admin_views import AdminOrderViewSet

router = DefaultRouter(trailing_slash=False)
router.register('orders', AdminOrderViewSet, basename='admin-order')

urlpatterns = [
    path('production/summary', ProductionSummaryView.as_view(), name='admin-production-summary'),
    path('production/placements', ProductionPlacementsView.as_view(), name='admin-production-placements'),
] + router.urls
