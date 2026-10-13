import logging

from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class BusinessError(drf_exceptions.APIException):
    """Base class for business-rule violations that carry an explicit error
    code for the client (see the unified error envelope in the API guide).
    """
    status_code = 422
    default_code = 'BUSINESS_RULE_VIOLATION'
    default_detail = _('The request violates a business rule.')

    def __init__(self, detail=None, code=None):
        self.code = code or self.default_code
        super().__init__(detail=detail or self.default_detail, code=self.code)


class OrderLockedError(BusinessError):
    status_code = 409
    default_code = 'ORDER_LOCKED'
    default_detail = _('The order can only be changed through its allowed actions in this state.')


class InvalidStatusTransitionError(BusinessError):
    status_code = 409
    default_code = 'INVALID_STATUS_TRANSITION'
    default_detail = _('This status transition is not allowed.')


class InUseError(BusinessError):
    status_code = 409
    default_code = 'IN_USE'
    default_detail = _('This record is in use and cannot be deleted.')


class EmailTakenError(BusinessError):
    status_code = 409
    default_code = 'EMAIL_TAKEN'
    default_detail = _('This e-mail is already in use.')


class LastAdminError(BusinessError):
    status_code = 409
    default_code = 'LAST_ADMIN'
    default_detail = _('The last admin cannot have their role removed or be deleted.')


class InvalidFileTokenError(BusinessError):
    status_code = 422
    default_code = 'INVALID_FILE_TOKEN'
    default_detail = _('The file link is invalid or has expired.')


class FileTooLargeError(BusinessError):
    status_code = 413
    default_code = 'FILE_TOO_LARGE'
    default_detail = _('The file is too large.')


class UnsupportedFileTypeError(BusinessError):
    status_code = 422
    default_code = 'UNSUPPORTED_FILE_TYPE'
    default_detail = _('Unsupported file type.')


# Fallback mapping for DRF/Django exceptions that don't carry our own `.code`
# attribute, checked via isinstance (ordered dict - first match wins).
_CODE_BY_EXCEPTION_TYPE = {
    drf_exceptions.AuthenticationFailed: 'AUTHENTICATION_FAILED',
    drf_exceptions.NotAuthenticated: 'AUTHENTICATION_REQUIRED',
    drf_exceptions.PermissionDenied: 'PERMISSION_DENIED',
    drf_exceptions.NotFound: 'NOT_FOUND',
    drf_exceptions.MethodNotAllowed: 'METHOD_NOT_ALLOWED',
    drf_exceptions.NotAcceptable: 'NOT_ACCEPTABLE',
    drf_exceptions.UnsupportedMediaType: 'UNSUPPORTED_MEDIA_TYPE',
    drf_exceptions.Throttled: 'THROTTLED',
    drf_exceptions.ParseError: 'PARSE_ERROR',
}


def _extract_errors(detail, prefix=''):
    """Walk DRF's (possibly nested) error detail structure, splitting it into
    general messages and a flat {"billing.zip": ["…"]} field-error map.
    """
    general = []
    fields = {}

    if isinstance(detail, list):
        for item in detail:
            if isinstance(item, (dict, list)):
                sub_general, sub_fields = _extract_errors(item, prefix)
                general.extend(sub_general)
                fields.update(sub_fields)
            elif prefix:
                fields.setdefault(prefix, []).append(str(item))
            else:
                general.append(str(item))
    elif isinstance(detail, dict):
        for key, value in detail.items():
            if not prefix and key in ('non_field_errors', 'detail'):
                sub_general, _unused = _extract_errors(value, '')
                general.extend(sub_general)
                continue
            path = f'{prefix}.{key}' if prefix else str(key)
            sub_general, sub_fields = _extract_errors(value, path)
            general.extend(sub_general)
            fields.update(sub_fields)
    elif prefix:
        fields.setdefault(prefix, []).append(str(detail))
    else:
        general.append(str(detail))

    return general, fields


def custom_exception_handler(exc, context):
    """Unified envelope: { "error": { "code", "message", "fields"? } }.

    Anything DRF's own handler doesn't recognize (a bug, not a client error)
    still gets answered as JSON in the same shape instead of falling through
    to Django's HTML error page - API clients should never have to branch on
    content type to parse an error.
    """
    response = drf_exception_handler(exc, context)

    if response is None:
        logger.exception('Unhandled exception in API view', exc_info=exc)
        return Response(
            {'error': {'code': 'SERVER_ERROR', 'message': str(_('An unexpected error occurred.'))}},
            status=500,
        )

    fields = {}
    if isinstance(exc, drf_exceptions.ValidationError):
        code = 'VALIDATION_ERROR'
        general, fields = _extract_errors(response.data)
        message = ' '.join(general) if general else str(_('The submitted data did not pass validation.'))
    else:
        code = getattr(exc, 'code', None)
        if not code:
            for exc_type, mapped_code in _CODE_BY_EXCEPTION_TYPE.items():
                if isinstance(exc, exc_type):
                    code = mapped_code
                    break
        code = code or 'ERROR'
        detail = response.data.get('detail', response.data) if isinstance(response.data, dict) else response.data
        message = str(detail)

    payload = {'error': {'code': code, 'message': message}}
    if fields:
        payload['error']['fields'] = fields
    response.data = payload
    return response
