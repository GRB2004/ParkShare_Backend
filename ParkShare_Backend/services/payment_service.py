from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import re
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from ParkShare_Backend.models import (
    AccountStatus,
    Pago,
    PaymentStatus,
    Reserva,
    ReservationStatus,
    RetiroCobro,
    UserRole,
    Usuario,
    WithdrawalStatus,
)
from ParkShare_Backend.puentes.payment_gateway import PaymentGatewayBridge, get_payment_gateway
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from ParkShare_Backend.services.reserva_service import (
    cancel_reservation,
    confirm_reservation,
)

MEXICO_TZ = ZoneInfo('America/Mexico_City')
TWO_PLACES = Decimal('0.01')

ALLOWED_PAYMENT_METHODS = (
    'TARJETA',
    'TARJETA_DEBITO',
    'TARJETA_CREDITO',
)
ALLOWED_MOVEMENT_STATUS_FILTERS = ('Pagado', 'Pendiente de uso')
PAYOUT_ACCOUNT_REGEX = re.compile(r'^\d{16,18}$')


def _quantize_money(value: Decimal) -> Decimal:
    return (value or Decimal('0.00')).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def mask_payout_account(account: str | None) -> str | None:
    """Mask a bank card or CLABE showing only the last 4 digits (e.g. '****7890')."""
    if account is None:
        return None
    cleaned = str(account).strip()
    if not cleaned:
        return None
    if len(cleaned) <= 4:
        return f'****{cleaned}'
    return f'****{cleaned[-4:]}'


def _check_active_tenant_for_payment(user: Usuario) -> None:
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.rol != UserRole.ARRENDATARIO
        or user.estado_cuenta != AccountStatus.ACTIVA
    ):
        raise PermissionDeniedError(
            'Solo un arrendatario con cuenta activa puede procesar pagos de reservaciones.'
        )


def _check_active_landlord_for_finances(user: Usuario) -> None:
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.rol != UserRole.ARRENDADOR
        or user.estado_cuenta != AccountStatus.ACTIVA
    ):
        raise PermissionDeniedError(
            'Se requiere una cuenta de arrendador activa y verificada para acceder al panel de cobros.'
        )


def validate_payment_method(metodo: str | None) -> str:
    if not isinstance(metodo, str) or not metodo.strip():
        raise DomainValidationError(
            'El método de pago es obligatorio.',
            errors={'metodo': ['Debes especificar un método de pago válido.']},
        )
    normalized = metodo.strip().upper()
    if normalized not in ALLOWED_PAYMENT_METHODS:
        raise DomainValidationError(
            'El método de pago indicado no es válido.',
            errors={'metodo': [f'Valores permitidos: {", ".join(ALLOWED_PAYMENT_METHODS)}.']},
        )
    return normalized


def process_reservation_payment(
    reservation_id: int,
    tenant: Usuario,
    metodo: str,
    token_pago: str | None = None,
    now: datetime | None = None,
    gateway: PaymentGatewayBridge | None = None,
) -> tuple[Pago, dict]:
    """Process a reservation payment atomically through the PaymentGatewayBridge (CU-07 / CU-06)."""
    _check_active_tenant_for_payment(tenant)
    normalized_method = validate_payment_method(metodo)

    if token_pago is not None and not isinstance(token_pago, str):
        raise DomainValidationError(
            'El token de pago debe ser una cadena de texto.',
            errors={'token_pago': ['Formato de token de pago inválido.']},
        )

    reference_now = now if now is not None else timezone.now()
    adapter = gateway or get_payment_gateway()

    with transaction.atomic():
        reservation = (
            Reserva.objects.select_related('cochera', 'arrendatario')
            .select_for_update()
            .filter(id_reserva=reservation_id)
            .first()
        )
        if reservation is None:
            raise NotFoundError('Reservación no encontrada.')

        if reservation.arrendatario_id != tenant.id_usuario:
            raise PermissionDeniedError(
                'No tienes permiso para pagar una reservación que no te pertenece.'
            )

        if Pago.objects.filter(reserva=reservation, estado=PaymentStatus.APROBADO).exists():
            raise ConflictError('La reservación ya cuenta con un pago aprobado.')

        if reservation.estado != ReservationStatus.SOLICITADA:
            raise ConflictError(
                f'No es posible procesar el pago para una reservación en estado {reservation.estado}.'
            )

        total_amount = _quantize_money(
            (reservation.monto_base or Decimal('0.00'))
            + (reservation.monto_penalizacion or Decimal('0.00'))
        )

        payment = Pago.objects.create(
            reserva=reservation,
            monto_total=total_amount,
            metodo=normalized_method,
            estado=PaymentStatus.PENDIENTE,
            fecha_pago=reference_now,
        )

        result = adapter.charge(
            amount=total_amount,
            method=normalized_method,
            token_pago=token_pago,
            metadata={'reserva_id': reservation.id_reserva, 'arrendatario_id': tenant.id_usuario},
        )

        if result.approved:
            payment.estado = PaymentStatus.APROBADO
            payment.referencia_pasarela = result.reference
            payment.save(update_fields=['estado', 'referencia_pasarela'])

            confirmed_reservation = confirm_reservation(
                reservation.id_reserva,
                by_system=True,
                now=reference_now,
            )
            payload = {
                'id_pago': payment.id_pago,
                'reserva_id': confirmed_reservation.id_reserva,
                'monto_total': f'{payment.monto_total:.2f}',
                'metodo': payment.metodo,
                'referencia_pasarela': payment.referencia_pasarela,
                'estado': payment.estado,
                'fecha_pago': payment.fecha_pago,
                'estado_reserva': confirmed_reservation.estado,
                'qr_entrada_generado': True,
                'mensaje': result.message,
            }
            return payment, payload

        payment.estado = PaymentStatus.RECHAZADO
        payment.referencia_pasarela = result.reference
        payment.save(update_fields=['estado', 'referencia_pasarela'])

        reservation.estado = ReservationStatus.CANCELADA
        reservation.save(update_fields=['estado'])

        payload = {
            'id_pago': payment.id_pago,
            'reserva_id': reservation.id_reserva,
            'monto_total': f'{payment.monto_total:.2f}',
            'metodo': payment.metodo,
            'referencia_pasarela': payment.referencia_pasarela,
            'estado': payment.estado,
            'fecha_pago': payment.fecha_pago,
            'estado_reserva': reservation.estado,
            'qr_entrada_generado': False,
            'detail': result.message,
        }
        return payment, payload


def apply_late_checkout_charge(reservation: Reserva) -> Pago | None:
    """Update the approved payment's monto_total to include any RN-03 late checkout surcharge."""
    with transaction.atomic():
        payment = (
            Pago.objects.select_for_update()
            .filter(reserva_id=reservation.id_reserva, estado=PaymentStatus.APROBADO)
            .first()
        )
        if payment is None:
            return None

        updated_total = _quantize_money(
            (reservation.monto_base or Decimal('0.00'))
            + (reservation.monto_penalizacion or Decimal('0.00'))
        )
        if payment.monto_total != updated_total:
            payment.monto_total = updated_total
            payment.save(update_fields=['monto_total'])
        return payment


def sync_cancellation_payment_if_exists(
    reservation: Reserva,
    gateway: PaymentGatewayBridge | None = None,
) -> Pago | None:
    """Synchronize an approved payment upon reservation cancellation (RN-04 full refund / RN-05 $100 penalty)."""
    adapter = gateway or get_payment_gateway()
    with transaction.atomic():
        payment = (
            Pago.objects.select_for_update()
            .filter(reserva_id=reservation.id_reserva, estado=PaymentStatus.APROBADO)
            .first()
        )
        if payment is None:
            return None

        penalty = _quantize_money(reservation.monto_penalizacion or Decimal('0.00'))
        if penalty <= Decimal('0.00'):
            adapter.refund(payment.referencia_pasarela, payment.monto_total)
            payment.estado = PaymentStatus.REEMBOLSADO
            payment.save(update_fields=['estado'])
        else:
            if payment.monto_total > penalty:
                adapter.refund(payment.referencia_pasarela, payment.monto_total - penalty)
            payment.monto_total = penalty
            payment.save(update_fields=['monto_total'])
        return payment


def handle_reservation_cancellation(
    reservation: Reserva,
    now: datetime | None = None,
    gateway: PaymentGatewayBridge | None = None,
) -> tuple[Reserva, Pago | None]:
    """Cancel a reservation applying RN-04 / RN-05 and update any associated approved payment."""
    if reservation.estado != ReservationStatus.CANCELADA:
        cancelled_reservation, _ = cancel_reservation(
            reservation.id_reserva,
            by_system=True,
            now=now,
        )
    else:
        cancelled_reservation = reservation

    payment = sync_cancellation_payment_if_exists(cancelled_reservation, gateway=gateway)
    return cancelled_reservation, payment


def get_host_financial_summary(
    host_user: Usuario,
    estado_filter: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Return the landlord's financial dashboard: ganado_hoy, disponible_para_retirar, cuenta_cobro, and movimientos."""
    _check_active_landlord_for_finances(host_user)

    normalized_filter = None
    if estado_filter is not None and str(estado_filter).strip() != '':
        normalized_filter = str(estado_filter).strip()
        if normalized_filter not in ALLOWED_MOVEMENT_STATUS_FILTERS:
            raise DomainValidationError(
                'El filtro de estado de movimientos no es válido.',
                errors={'estado': ["Valores permitidos: 'Pagado', 'Pendiente de uso'."]},
            )

    reference_now = now if now is not None else timezone.now()
    local_today = reference_now.astimezone(MEXICO_TZ).date()

    payments = (
        Pago.objects.select_related('reserva__cochera')
        .filter(
            reserva__cochera__arrendador_id=host_user.id_usuario,
            estado=PaymentStatus.APROBADO,
        )
        .order_by('-fecha_pago', '-id_pago')
    )

    earned_today = Decimal('0.00')
    total_released = Decimal('0.00')
    total_penalties = Decimal('0.00')
    all_movements: list[dict] = []

    for payment in payments:
        res = payment.reserva
        penalty_amount = _quantize_money(res.monto_penalizacion)

        if res.estado == ReservationStatus.FINALIZADA:
            visual_status = 'Pagado'
            amount = _quantize_money(payment.monto_total)
            base_for_movement = _quantize_money(res.monto_base)
            total_released += amount
            total_penalties += penalty_amount

            completion_dt = res.hora_salida_real or res.fecha_hora_fin
            if completion_dt and completion_dt.astimezone(MEXICO_TZ).date() == local_today:
                earned_today += amount
        elif res.estado == ReservationStatus.CANCELADA and penalty_amount > Decimal('0.00'):
            visual_status = 'Pagado'
            amount = _quantize_money(payment.monto_total)
            base_for_movement = Decimal('0.00')
            total_released += amount
            total_penalties += penalty_amount

            cancel_dt = payment.fecha_pago or res.fecha_hora_inicio
            if cancel_dt and cancel_dt.astimezone(MEXICO_TZ).date() == local_today:
                earned_today += amount
        elif res.estado in (ReservationStatus.CONFIRMADA, ReservationStatus.EN_USO):
            visual_status = 'Pendiente de uso'
            amount = _quantize_money(payment.monto_total)
            base_for_movement = _quantize_money(res.monto_base)
        else:
            continue

        start_local = res.fecha_hora_inicio.astimezone(MEXICO_TZ)
        end_local = res.fecha_hora_fin.astimezone(MEXICO_TZ)
        schedule_label = f"{start_local.strftime('%Y-%m-%d %H:%M')} - {end_local.strftime('%H:%M')}"

        movement_item = {
            'id_pago': payment.id_pago,
            'reserva_id': res.id_reserva,
            'cochera': res.cochera.nombre,
            'fecha_horario': schedule_label,
            'monto': f'{amount:.2f}',
            'monto_base': f'{base_for_movement:.2f}',
            'monto_penalizacion': f'{penalty_amount:.2f}',
            'multa_acumulada': f'{penalty_amount:.2f}',
            'estado': visual_status,
            'estado_reserva': res.estado,
            'fecha_pago': payment.fecha_pago,
        }
        if normalized_filter is None or visual_status == normalized_filter:
            all_movements.append(movement_item)

    withdrawn_total = (
        RetiroCobro.objects.filter(
            arrendador_id=host_user.id_usuario,
            estado=WithdrawalStatus.COMPLETADO,
        ).aggregate(total=Coalesce(Sum('monto'), Decimal('0.00')))['total']
        or Decimal('0.00')
    )

    available = max(Decimal('0.00'), _quantize_money(total_released - withdrawn_total))

    return {
        'ganado_hoy': f'{_quantize_money(earned_today):.2f}',
        'disponible_para_retirar': f'{available:.2f}',
        'multa_acumulada': f'{_quantize_money(total_penalties):.2f}',
        'cuenta_cobro': mask_payout_account(host_user.cuenta_cobro),
        'movimientos': all_movements,
    }


def withdraw_host_balance(
    host_user: Usuario,
    cuenta_cobro: str | None = None,
    now: datetime | None = None,
    gateway: PaymentGatewayBridge | None = None,
) -> dict:
    """Atomically withdraw all available released funds to the landlord's payout account (CU-07)."""
    _check_active_landlord_for_finances(host_user)
    reference_now = now if now is not None else timezone.now()
    adapter = gateway or get_payment_gateway()

    with transaction.atomic():
        locked_host = Usuario.objects.select_for_update().get(id_usuario=host_user.id_usuario)

        if cuenta_cobro is not None and str(cuenta_cobro).strip() != '':
            candidate_account = str(cuenta_cobro).strip()
            if not PAYOUT_ACCOUNT_REGEX.match(candidate_account):
                raise DomainValidationError(
                    'Debes registrar una cuenta de cobro válida antes de solicitar un retiro.',
                    errors={'cuenta_cobro': ['La cuenta de cobro debe contener entre 16 y 18 dígitos numéricos.']},
                    error_code='MissingPayoutAccount',
                )
            if locked_host.cuenta_cobro != candidate_account:
                locked_host.cuenta_cobro = candidate_account
                locked_host.save(update_fields=['cuenta_cobro'])
                host_user.cuenta_cobro = candidate_account
        else:
            candidate_account = (locked_host.cuenta_cobro or '').strip()
            if not candidate_account or not PAYOUT_ACCOUNT_REGEX.match(candidate_account):
                raise DomainValidationError(
                    'Debes registrar una cuenta de cobro válida antes de solicitar un retiro.',
                    errors={'cuenta_cobro': ['No hay una cuenta de cobro válida registrada.']},
                    error_code='MissingPayoutAccount',
                )

        released_total = (
            Pago.objects.filter(
                reserva__cochera__arrendador_id=locked_host.id_usuario,
                estado=PaymentStatus.APROBADO,
            )
            .filter(
                Q(reserva__estado=ReservationStatus.FINALIZADA)
                | Q(
                    reserva__estado=ReservationStatus.CANCELADA,
                    reserva__monto_penalizacion__gt=Decimal('0.00'),
                )
            )
            .aggregate(total=Coalesce(Sum('monto_total'), Decimal('0.00')))['total']
            or Decimal('0.00')
        )
        withdrawn_total = (
            RetiroCobro.objects.filter(
                arrendador_id=locked_host.id_usuario,
                estado=WithdrawalStatus.COMPLETADO,
            ).aggregate(total=Coalesce(Sum('monto'), Decimal('0.00')))['total']
            or Decimal('0.00')
        )
        available = _quantize_money(released_total - withdrawn_total)

        if available <= Decimal('0.00'):
            raise ConflictError(
                'No tienes saldo disponible para retirar en este momento.',
                error_code='NoFundsAvailable',
            )

        payout_result = adapter.payout(
            destination_account=candidate_account,
            amount=available,
            metadata={'arrendador_id': locked_host.id_usuario},
        )
        if not payout_result.approved:
            raise ConflictError(
                payout_result.message or 'No fue posible procesar el retiro en este momento.',
                error_code='PayoutRejected',
            )

        withdrawal = RetiroCobro.objects.create(
            arrendador=locked_host,
            cuenta_cobro=candidate_account,
            monto=available,
            referencia=payout_result.reference,
            estado=WithdrawalStatus.COMPLETADO,
            fecha_retiro=reference_now,
        )

        return {
            'id_retiro': withdrawal.id_retiro,
            'monto_retirado': f'{withdrawal.monto:.2f}',
            'cuenta_cobro': mask_payout_account(candidate_account),
            'referencia': withdrawal.referencia,
            'estado': withdrawal.estado,
            'fecha_retiro': withdrawal.fecha_retiro,
            'disponible_para_retirar': '0.00',
            'mensaje': 'Retiro procesado correctamente hacia tu cuenta de cobro.',
        }
