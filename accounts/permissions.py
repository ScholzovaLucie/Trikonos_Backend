from rest_framework.permissions import BasePermission

from .models import StaffRole


class IsStaffOrAdmin(BasePermission):
    """ADMIN and STAFF both pass - used on endpoints any admin-panel user
    may call (orders, customers, dashboard, read-only lookups/motifs).
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in (StaffRole.ADMIN, StaffRole.STAFF))


class IsAdmin(BasePermission):
    """ADMIN only - lookup/motif writes, user management, REFUNDED and
    in-production cancellation.
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == StaffRole.ADMIN)
