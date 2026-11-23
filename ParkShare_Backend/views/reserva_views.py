from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ParkShare_Backend.permissions import IsActiveAccount, IsActiveTenant
from ParkShare_Backend.serializers.reserva_serializers import (
    ReservaCreateSerializer,
    ReservaCreationResponseSerializer,
    ReservaDetailSerializer,
    ReservaListSerializer,
    ReservaPatchResponseSerializer,
    ReservaStateUpdateSerializer,
)
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from ParkShare_Backend.services.reserva_service import (
    cancel_reservation,
    create_reservation,
    get_reservation_detail_for_user,
    list_reservations_for_user,
    transition_reservation_state,
)
from ParkShare_Backend.views.auth_views import (
    StandardPagination,
    handle_domain_exception,
    validation_error_response,
)


class ReservationListCreateView(APIView):
    """List user reservations (`GET /reservas`) or create a reservation (`POST /reservas`)."""

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated(), IsActiveTenant()]
        return [IsAuthenticated(), IsActiveAccount()]

    def get(self, request):
        try:
            reservations = list_reservations_for_user(
                request.user,
                view_filter=request.query_params.get('vista'),
                status_filter=request.query_params.get('estado'),
            )
        except (DomainValidationError, PermissionDeniedError) as exc:
            return handle_domain_exception(exc)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(reservations, request, view=self)
        serializer = ReservaListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = ReservaCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        data = serializer.validated_data
        try:
            reservation = create_reservation(
                tenant=request.user,
                garage_id=data['cochera_id'],
                start_dt=data['fecha_hora_inicio'],
                end_dt=data['fecha_hora_fin'],
            )
        except (DomainValidationError, PermissionDeniedError, NotFoundError, ConflictError) as exc:
            return handle_domain_exception(exc)

        return Response(
            ReservaCreationResponseSerializer(reservation).data,
            status=status.HTTP_201_CREATED,
        )


class ReservationDetailUpdateView(APIView):
    """Retrieve reservation detail (`GET /reservas/{id}`) or apply state transition (`PATCH /reservas/{id}`)."""

    permission_classes = [IsAuthenticated, IsActiveAccount]

    def get(self, request, pk: int):
        try:
            reservation, metrics = get_reservation_detail_for_user(pk, request.user)
        except (NotFoundError, PermissionDeniedError) as exc:
            return handle_domain_exception(exc)

        serializer = ReservaDetailSerializer(reservation, context={'metrics': metrics})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk: int):
        serializer = ReservaStateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        data = serializer.validated_data
        try:
            reservation = transition_reservation_state(
                reservation_id=pk,
                actor=request.user,
                target_status=data['estado'],
                real_checkout_dt=data.get('hora_salida_real'),
            )
        except (DomainValidationError, PermissionDeniedError, NotFoundError, ConflictError) as exc:
            return handle_domain_exception(exc)

        return Response(
            ReservaPatchResponseSerializer(reservation).data,
            status=status.HTTP_200_OK,
        )


class ReservationCancelView(APIView):
    """Cancel a SOLICITADA or CONFIRMADA reservation applying RN-04 / RN-05 (`POST /reservas/{id}/cancelar`)."""

    permission_classes = [IsAuthenticated, IsActiveAccount]

    def post(self, request, pk: int):
        try:
            reservation, penalty_applied = cancel_reservation(pk, actor=request.user)
        except (DomainValidationError, PermissionDeniedError, NotFoundError, ConflictError) as exc:
            return handle_domain_exception(exc)

        message = (
            'Reserva cancelada con multa por cancelación tardía.'
            if penalty_applied
            else 'Reserva cancelada sin cargo adicional.'
        )
        return Response(
            {
                'mensaje': message,
                'id_reserva': reservation.id_reserva,
                'estado': reservation.estado,
                'monto_base': f'{reservation.monto_base:.2f}',
                'monto_penalizacion': f'{reservation.monto_penalizacion:.2f}',
                'aplico_multa_tardia': penalty_applied,
            },
            status=status.HTTP_200_OK,
        )

