from django.urls import path

from .admin_views import AdminDashboardView, AdminSearchView

urlpatterns = [
    path('dashboard', AdminDashboardView.as_view(), name='admin-dashboard'),
    path('search', AdminSearchView.as_view(), name='admin-search'),
]
