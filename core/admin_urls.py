from django.urls import path

from .admin_views import AdminSearchView

urlpatterns = [
    path('search', AdminSearchView.as_view(), name='admin-search'),
]
