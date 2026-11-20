from rest_framework.permissions import BasePermission

from .models import AccountStatus, UserRole


class IsAdminRole(BasePermission):
    """Allows access only to active administrator accounts."""

    message = 'No tienes permisos de administrador para realizar esta acción.'

    def has_permission(self, request, view) -> bool:
        user = getattr(request, 'user', None)
        return bool(
            user
            and getattr(user, 'is_authenticated', False)
            and user.rol == UserRole.ADMIN
            and user.estado_cuenta == AccountStatus.ACTIVA
        )


class IsActiveAccount(BasePermission):
    """Blocks business operations when account is PENDIENTE, RECHAZADA, or SUSPENDIDA (RN-08)."""

    message = 'Tu cuenta debe estar activa para realizar esta operación.'

    def has_permission(self, request, view) -> bool:
        user = getattr(request, 'user', None)
        return bool(
            user
            and getattr(user, 'is_authenticated', False)
            and user.estado_cuenta == AccountStatus.ACTIVA
        )


class IsActiveLandlord(BasePermission):
    """Allows access only to active ARRENDADOR accounts."""

    message = 'Se requiere una cuenta de arrendador activa para realizar esta acción.'

    def has_permission(self, request, view) -> bool:
        user = getattr(request, 'user', None)
        return bool(
            user
            and getattr(user, 'is_authenticated', False)
            and user.rol == UserRole.ARRENDADOR
            and user.estado_cuenta == AccountStatus.ACTIVA
        )


class IsActiveTenant(BasePermission):
    """Allows access only to active ARRENDATARIO accounts."""

    message = 'Se requiere una cuenta de arrendatario activa para realizar esta acción.'

    def has_permission(self, request, view) -> bool:
        user = getattr(request, 'user', None)
        return bool(
            user
            and getattr(user, 'is_authenticated', False)
            and user.rol == UserRole.ARRENDATARIO
            and user.estado_cuenta == AccountStatus.ACTIVA
        )
