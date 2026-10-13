from django.conf import settings

from core.emails import send_templated_email


def send_password_setup_email(user, uid, token):
    url = f'{settings.FRONTEND_URL}/admin/nastavit-heslo?uid={uid}&token={token}'
    send_templated_email(
        'password_setup',
        {'user_name': user.name, 'url': url},
        subject='Nastavení hesla - administrace Trikonoš.cz',
        recipient_list=[user.email],
    )
