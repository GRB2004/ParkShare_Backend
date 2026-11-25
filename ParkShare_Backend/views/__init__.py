from django.http import JsonResponse

from .admin_auth_views import (
    AdminAccountDetailView,
    AdminAccountListView,
    AdminAccountSuspendView,
    AdminLandlordRequestDetailView,
    AdminLandlordRequestListView,
)
from .auth_views import (
    AttachmentUploadView,
    LoginView,
    MediaAttachmentServeView,
    RegisterView,
)
from .claims_reviews_views import (
    AdminAppealResolveView,
    AdminClaimAppealListView,
    AdminComplaintDetailResolveView,
    ComplaintCreateView,
    LandlordSanctionDetailView,
    LandlordSanctionListView,
    ReservationReviewCreateView,
    SanctionAppealCreateView,
)
from .cochera_views import (
    AdminGarageListView,
    AdminGarageValidationView,
    GarageDetailUpdateView,
    GarageListCreateView,
    LandlordGarageListView,
)
from .payment_views import HostEarningsView, HostWithdrawView, ProcessPaymentView
from .qr_views import ReservationQRDetailView, SimulateQRView, ValidateQRView
from .reserva_views import (
    ReservationCancelView,
    ReservationDetailUpdateView,
    ReservationListCreateView,
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
    'AttachmentUploadView',
    'MediaAttachmentServeView',
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
    'AdminGarageListView',
    'AdminGarageValidationView',
    'ReservationListCreateView',
    'ReservationDetailUpdateView',
    'ReservationCancelView',
    'ReservationQRDetailView',
    'ValidateQRView',
    'SimulateQRView',
    'ProcessPaymentView',
    'HostEarningsView',
    'HostWithdrawView',
    'ReservationReviewCreateView',
    'ComplaintCreateView',
    'AdminClaimAppealListView',
    'AdminComplaintDetailResolveView',
    'LandlordSanctionListView',
    'LandlordSanctionDetailView',
    'SanctionAppealCreateView',
    'AdminAppealResolveView',
]
