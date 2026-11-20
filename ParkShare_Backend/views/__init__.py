from django.http import JsonResponse

from .admin_auth_views import (
    AdminAccountDetailView,
    AdminAccountListView,
    AdminAccountSuspendView,
    AdminLandlordRequestDetailView,
    AdminLandlordRequestListView,
)
from .auth_views import LoginView, RegisterView
from .cochera_views import (
    AdminGarageValidationView,
    GarageDetailUpdateView,
    GarageListCreateView,
    LandlordGarageListView,
)


def home_view(request):
    """Initial healthcheck endpoint for ParkShare API."""
    return JsonResponse({
        "status": "success",
        "message": "Bienvenido a la API de ParkShare Backend",
        "database": "MySQL conectado"
    })


__all__ = [
    'home_view',
    'RegisterView',
    'LoginView',
    'AdminLandlordRequestListView',
    'AdminLandlordRequestDetailView',
    'AdminAccountListView',
    'AdminAccountDetailView',
    'AdminAccountSuspendView',
    'GarageListCreateView',
    'LandlordGarageListView',
    'GarageDetailUpdateView',
    'AdminGarageValidationView',
]
