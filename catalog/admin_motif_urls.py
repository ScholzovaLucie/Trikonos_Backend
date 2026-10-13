from rest_framework.routers import DefaultRouter

from .admin_motif_views import AdminMotifViewSet

router = DefaultRouter(trailing_slash=False)
router.register('motifs', AdminMotifViewSet, basename='admin-motif')

urlpatterns = router.urls
