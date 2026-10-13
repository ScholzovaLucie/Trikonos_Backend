from rest_framework.routers import DefaultRouter

from .admin_views import AdminCarrierViewSet

router = DefaultRouter(trailing_slash=False)
router.register('carriers', AdminCarrierViewSet, basename='admin-carrier')

urlpatterns = router.urls
