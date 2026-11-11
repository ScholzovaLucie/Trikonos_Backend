from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """Every backend account is an admin account - there is no second
    (STAFF) tier. This just means "logged in to the admin panel".
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated)
