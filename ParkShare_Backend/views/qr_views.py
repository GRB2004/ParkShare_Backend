from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ParkShare_Backend.permissions import IsActiveAccount, IsActiveLandlord
from ParkShare_Backend.serializers.qr_serializers import (
    CodigoQRDetailSerializer,
    ValidateQRInputSerializer,
)
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    GoneError,
    NotFoundError,
    PermissionDeniedError,
)
from ParkShare_Backend.services.qr_service import (
    get_active_qr_for_reservation,
    validate_scanned_qr,
)
from ParkShare_Backend.views.auth_views import (
    handle_domain_exception,
    validation_error_response,
)


class ReservationQRDetailView(APIView):
    """Retrieve the active ENTRADA or SALIDA QR token for a reservation (`GET /reservas/{id}/qr`)."""

    permission_classes = [IsAuthenticated, IsActiveAccount]

    def get(self, request, pk: int):
        try:
            qr = get_active_qr_for_reservation(pk, request.user)
        except (PermissionDeniedError, NotFoundError, ConflictError) as exc:
            return handle_domain_exception(exc)

        return Response(CodigoQRDetailSerializer(qr).data, status=status.HTTP_200_OK)


class ValidateQRView(APIView):
    """Validate scanned entry or exit QR token for garage owners (`POST /reservas/validar`)."""

    permission_classes = [IsAuthenticated, IsActiveLandlord]

    def post(self, request):
        serializer = ValidateQRInputSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            result = validate_scanned_qr(
                token=serializer.validated_data['token'],
                host_user=request.user,
            )
        except (
            DomainValidationError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
            GoneError,
        ) as exc:
            return handle_domain_exception(exc)

        return Response(result, status=status.HTTP_200_OK)
