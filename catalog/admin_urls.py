from rest_framework.routers import DefaultRouter

from .admin_views import (
    AdminCategoryViewSet,
    AdminColorViewSet,
    AdminCutViewSet,
    AdminPrintAreaViewSet,
    AdminProductTypeViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register('categories', AdminCategoryViewSet, basename='admin-category')
router.register('colors', AdminColorViewSet, basename='admin-color')
router.register('cuts', AdminCutViewSet, basename='admin-cut')
router.register('product-types', AdminProductTypeViewSet, basename='admin-product-type')
router.register('print-areas', AdminPrintAreaViewSet, basename='admin-print-area')

urlpatterns = router.urls
