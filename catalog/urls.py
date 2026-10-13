from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import CatalogOptionsView, CategoryViewSet, MotifViewSet

router = DefaultRouter(trailing_slash=False)
router.register('categories', CategoryViewSet, basename='category')
router.register('motifs', MotifViewSet, basename='motif')

urlpatterns = [
    path('catalog/options', CatalogOptionsView.as_view(), name='catalog-options'),
] + router.urls
