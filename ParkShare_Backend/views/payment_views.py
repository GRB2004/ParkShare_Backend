from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ParkShare_Backend.models import PaymentStatus
from ParkShare_Backend.serializers.payment_serializers import (
    ProcessPaymentSerializer,
    WithdrawBalanceSerializer,
)
from ParkShare_Backend.services.auth_service import (
    AccountSuspendedError,
    AuthenticationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from ParkShare_Backend.services.payment_service import (
    get_host_financial_summary,
    process_reservation_payment,
    withdraw_host_balance,
)
from ParkShare_Backend.views.auth_views import (
    handle_domain_exception,
    validation_error_response,
)


class ProcessPaymentView(APIView):
    """POST /pagos — Process a reservation payment via the payment gateway bridge (CU-07)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ProcessPaymentSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            payment, payload = process_reservation_payment(
                reservation_id=serializer.validated_data['reserva_id'],
                tenant=request.user,
                metodo=serializer.validated_data['metodo'],
                token_pago=serializer.validated_data.get('token_pago'),
            )
            if hasattr(payload.get('fecha_pago'), 'isoformat'):
                payload['fecha_pago'] = payload['fecha_pago'].isoformat()

            http_status = (
                status.HTTP_201_CREATED
                if payment.estado == PaymentStatus.APROBADO
                else status.HTTP_402_PAYMENT_REQUIRED
            )
            return Response(payload, status=http_status)
        except (
            DomainValidationError,
            AuthenticationError,
            AccountSuspendedError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
        ) as exc:
            return handle_domain_exception(exc)


class HostEarningsView(APIView):
    """GET /arrendador/cobros — Retrieve landlord earnings dashboard and movements (CU-07)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            summary = get_host_financial_summary(
                host_user=request.user,
                estado_filter=request.query_params.get('estado'),
            )
            for item in summary.get('movimientos', []):
                if hasattr(item.get('fecha_pago'), 'isoformat'):
                    item['fecha_pago'] = item['fecha_pago'].isoformat()
            return Response(summary, status=status.HTTP_200_OK)
        except (
            DomainValidationError,
            AuthenticationError,
            AccountSuspendedError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
        ) as exc:
            return handle_domain_exception(exc)


class HostWithdrawView(APIView):
    """POST /arrendador/cobros/retirar — Withdraw available balance to payout account (CU-07)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WithdrawBalanceSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            result = withdraw_host_balance(
                host_user=request.user,
                cuenta_cobro=serializer.validated_data.get('cuenta_cobro'),
            )
            if hasattr(result.get('fecha_retiro'), 'isoformat'):
                result['fecha_retiro'] = result['fecha_retiro'].isoformat()
            return Response(result, status=status.HTTP_201_CREATED)
        except (
            DomainValidationError,
            AuthenticationError,
            AccountSuspendedError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
        ) as exc:
            return handle_domain_exception(exc)
