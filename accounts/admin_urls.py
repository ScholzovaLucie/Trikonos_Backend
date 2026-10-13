from rest_framework.routers import DefaultRouter

from .admin_views import AdminUserViewSet

router = DefaultRouter(trailing_slash=False)
router.register('users', AdminUserViewSet, basename='admin-user')

urlpatterns = router.urls
