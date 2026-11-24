from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ParkShare_Backend.permissions import IsActiveAccount, IsActiveLandlord
from ParkShare_Backend.serializers.qr_serializers import (
    ActiveSimulationQRSerializer,
    CodigoQRDetailSerializer,
    SimulateQRInputSerializer,
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
    list_active_qrs_for_simulation,
    simulate_qr_scan,
    validate_scanned_qr,
)
from ParkShare_Backend.views.admin_auth_views import StandardPagination
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


class SimulateQRView(APIView):
    """List active scannable QRs or simulate a QR scan for garage owners (`GET/POST /reservas/simular-qr`)."""

    permission_classes = [IsAuthenticated, IsActiveLandlord]

    def get(self, request):
        try:
            queryset = list_active_qrs_for_simulation(
                host_user=request.user,
                qr_type=request.query_params.get('tipo_qr'),
            )
        except (DomainValidationError, PermissionDeniedError) as exc:
            return handle_domain_exception(exc)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = ActiveSimulationQRSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = SimulateQRInputSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            result = simulate_qr_scan(
                host_user=request.user,
                token=serializer.validated_data.get('token'),
                reserva_id=serializer.validated_data.get('reserva_id'),
                qr_type=serializer.validated_data.get('tipo_qr'),
                respect_window=serializer.validated_data.get('respetar_ventana', False),
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
