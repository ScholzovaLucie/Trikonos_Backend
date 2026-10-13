from django.urls import path

from .views import (
    ChangePasswordView,
    CsrfView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
)

urlpatterns = [
    path('auth/csrf', CsrfView.as_view(), name='auth-csrf'),
    path('auth/login', LoginView.as_view(), name='auth-login'),
    path('auth/logout', LogoutView.as_view(), name='auth-logout'),
    path('auth/me', MeView.as_view(), name='auth-me'),
    path('auth/password', ChangePasswordView.as_view(), name='auth-password'),
    path('auth/password-reset', PasswordResetRequestView.as_view(), name='auth-password-reset'),
    path('auth/password-reset/confirm', PasswordResetConfirmView.as_view(), name='auth-password-reset-confirm'),
]
