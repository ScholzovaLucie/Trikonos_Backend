from django.urls import path

from .views import ConfigView, ContactView

urlpatterns = [
    path('config', ConfigView.as_view(), name='config'),
    path('contact', ContactView.as_view(), name='contact'),
]
