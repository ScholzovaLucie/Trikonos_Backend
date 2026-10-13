from django.urls import path
from rest_framework.routers import DefaultRouter

from .admin_views import AdminCustomerViewSet, CustomerExportConsentsView

router = DefaultRouter(trailing_slash=False)
router.register('customers', AdminCustomerViewSet, basename='admin-customer')

urlpatterns = [
    path('customers/export-consents', CustomerExportConsentsView.as_view(), name='admin-customer-export-consents'),
] + router.urls
