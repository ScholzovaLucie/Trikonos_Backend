from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.tokens import default_token_generator
from django.middleware.csrf import get_token
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import exceptions as drf_exceptions
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .emails import send_password_setup_email
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    MeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
)

User = get_user_model()

_TAG = ['Admin - přihlášení']


class CsrfView(APIView):
    """Nastaví CSRF cookie - volat jako první krok před přihlášením."""
    permission_classes = [AllowAny]

    @extend_schema(
        tags=_TAG,
        summary='Získat CSRF cookie',
        description=(
            'Zavolej jako úplně první request administrace, ještě před `POST '
            '/auth/login`. Session autentizace vyžaduje platný CSRF token na každý '
            'nebezpečný požadavek (POST/PATCH/PUT/DELETE) - tento endpoint nastaví '
            'cookie `csrftoken` v odpovědi. Hodnotu z cookie si frontend musí přečíst '
            '(JS má k ní přístup, cookie není httpOnly) a poslat v headeru '
            '`X-CSRFToken` u všech dalších nebezpečných requestů na `/admin/...`. '
            'Žádné tělo požadavku ani odpovědi.'
        ),
        request=None,
        responses={204: OpenApiResponse(description='CSRF cookie nastavena v odpovědi.')},
    )
    def get(self, request):
        get_token(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LoginView(APIView):
    """Přihlášení e-mailem a heslem, založí session."""
    permission_classes = [AllowAny]
    throttle_scope = 'login'

    @extend_schema(
        tags=_TAG,
        summary='Přihlásit administrátora/staffa',
        description=(
            'Přihlášení e-mailem a heslem. Při úspěchu založí session (cookie '
            '`sessionid`, httpOnly) a vrátí profil přihlášeného uživatele - podle '
            '`role` v odpovědi frontend skrývá/zobrazuje části administrace (viz '
            'tabulka oprávnění ADMIN/STAFF). Nesprávný e-mail i nesprávné heslo vrací '
            'stejnou chybu 401, aby se nedalo zjistit, jestli účet s daným e-mailem '
            'vůbec existuje. Limit 5 pokusů/min na IP (`ScopedRateThrottle`) - při '
            'překročení 429. Před tímto voláním musí proběhnout `GET /auth/csrf`.'
        ),
        request=LoginSerializer,
        responses={
            200: MeSerializer,
            401: OpenApiResponse(description='Nesprávný e-mail nebo heslo.'),
            429: OpenApiResponse(description='Překročen limit pokusů o přihlášení.'),
        },
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            request,
            email=serializer.validated_data['email'],
            password=serializer.validated_data['password'],
        )
        if user is None or not user.is_active:
            # Same message either way - never reveal which part was wrong.
            raise drf_exceptions.AuthenticationFailed('Nesprávný e-mail nebo heslo.')

        login(request, user)
        return Response(MeSerializer(user).data)


class LogoutView(APIView):
    """Odhlášení - zruší aktuální session."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=_TAG,
        summary='Odhlásit se',
        description='Zruší session aktuálně přihlášeného uživatele. Bez těla požadavku.',
        request=None,
        responses={204: OpenApiResponse(description='Session zrušena.')},
    )
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """Profil aktuálně přihlášeného uživatele."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=_TAG,
        summary='Kdo je přihlášený',
        description=(
            'Vrátí `id`, `email`, `name` a `role` aktuálně přihlášeného uživatele. '
            'Frontend si tím po načtení stránky (nebo po refreshi) ověří, že session '
            'je stále platná, a podle `role` rozhodne, co v administraci zobrazit - '
            'STAFF nevidí správu uživatelů ani možnost REFUNDED/zrušení ve výrobě.'
        ),
        responses={200: MeSerializer},
    )
    def get(self, request):
        return Response(MeSerializer(request.user).data)


class ChangePasswordView(APIView):
    """Změna vlastního hesla přihlášeným uživatelem."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=_TAG,
        summary='Změnit si vlastní heslo',
        description=(
            'Uživatel si sám změní heslo - musí znát současné heslo (`currentPassword`), '
            'jinak 400 s chybou u pole `currentPassword`. Nové heslo (`newPassword`) '
            'musí splnit Django validátory hesel (minimální délka, není příliš '
            'podobné e-mailu/jménu, není v seznamu běžných hesel, není čistě '
            'numerické) - chyby vrací pole `newPassword` jako seznam zpráv.'
        ),
        request=ChangePasswordSerializer,
        responses={204: OpenApiResponse(description='Heslo změněno.')},
    )
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data['new_password'])
        request.user.save(update_fields=['password'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(APIView):
    """POST /auth/password-reset - always 204, regardless of whether the
    e-mail matches an account, so the response can't be used to enumerate
    admin accounts.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=_TAG,
        summary='Vyžádat odkaz na reset hesla',
        description=(
            'Pro "Zapomenuté heslo" na přihlašovací obrazovce administrace. Pokud e-mail '
            'patří existujícímu uživateli, pošle mu e-mail s odkazem na nastavení '
            'nového hesla (platnost řeší `PasswordResetTokenGenerator` z Django, '
            'typicky pár dní). **Vždy vrací 204**, i když e-mail neexistuje - jinak by '
            'šlo podle rozdílné odpovědi zjistit, které e-maily v administraci existují.'
        ),
        request=PasswordResetRequestSerializer,
        responses={204: OpenApiResponse(description='E-mail odeslán, pokud účet s daným e-mailem existuje.')},
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = User.objects.filter(email__iexact=serializer.validated_data['email']).first()
        if user is not None:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            send_password_setup_email(user, uid, token)

        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetConfirmView(APIView):
    """Dokončení resetu hesla - nastaví nové heslo podle odkazu z e-mailu."""
    permission_classes = [AllowAny]

    @extend_schema(
        tags=_TAG,
        summary='Nastavit nové heslo podle odkazu z e-mailu',
        description=(
            'Druhý krok resetu hesla. `uid` a `token` jsou z odkazu v e-mailu, který '
            'poslal `POST /auth/password-reset` (frontend je z URL query stringu '
            'přečte a poslal v těle požadavku). Neplatný nebo prošlý token vrací 400 '
            's obecnou zprávou "Odkaz pro nastavení hesla je neplatný nebo prošlý." - '
            'frontend by v tom případě měl nabídnout znovu vyžádat reset. Nové heslo '
            'podléhá stejným Django validátorům jako `POST /auth/password`.'
        ),
        request=PasswordResetConfirmSerializer,
        responses={204: OpenApiResponse(description='Heslo nastaveno, uživatel se může přihlásit.')},
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        user.set_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password'])
        return Response(status=status.HTTP_204_NO_CONTENT)
