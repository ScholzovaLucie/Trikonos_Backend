from django.urls import path

from .views import (
    CartValidateView,
    DesignUploadView,
    OrderAccessView,
    OrderCreateView,
    OrderLookupView,
)

urlpatterns = [
    path('cart/validate', CartValidateView.as_view(), name='cart-validate'),
    path('uploads/designs', DesignUploadView.as_view(), name='design-upload'),
    path('orders', OrderCreateView.as_view(), name='order-create'),
    path('orders/lookup', OrderLookupView.as_view(), name='order-lookup'),
    path('orders/access/<str:access_token>', OrderAccessView.as_view(), name='order-access'),
]
