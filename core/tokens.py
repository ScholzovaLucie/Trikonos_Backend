"""Signed, DB-column-free tokens used across the public API:
- file tokens (django.core.signing, 7 days) returned by POST /uploads/designs
  and consumed when placing an order with a custom design.
- order access tokens (TimestampSigner, 180 days) returned by POST /orders
  and resolved by GET /orders/access/{accessToken}.
Both avoid needing a dedicated DB column just to look up a short-lived link.
"""
from django.core import signing
from django.utils.translation import gettext as _

from core.exceptions import InvalidFileTokenError

FILE_TOKEN_SALT = 'trikonos.design-upload'
FILE_TOKEN_MAX_AGE = 7 * 24 * 60 * 60

ACCESS_TOKEN_SALT = 'trikonos.order-access'
ACCESS_TOKEN_MAX_AGE = 180 * 24 * 60 * 60


def sign_file_token(payload):
    return signing.dumps(payload, salt=FILE_TOKEN_SALT)


def resolve_file_token(token):
    try:
        return signing.loads(token, salt=FILE_TOKEN_SALT, max_age=FILE_TOKEN_MAX_AGE)
    except signing.SignatureExpired:
        raise InvalidFileTokenError(_('The file link has expired, please upload the file again.'))
    except signing.BadSignature:
        raise InvalidFileTokenError()


def sign_order_access_token(order_id):
    return signing.TimestampSigner(salt=ACCESS_TOKEN_SALT).sign(str(order_id))


def resolve_order_access_token(token):
    """Returns the order id as a string, or raises signing.BadSignature /
    signing.SignatureExpired - callers turn that into a 404 (never reveal
    which part of the token was wrong).
    """
    return signing.TimestampSigner(salt=ACCESS_TOKEN_SALT).unsign(token, max_age=ACCESS_TOKEN_MAX_AGE)
