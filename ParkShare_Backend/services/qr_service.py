import hashlib
import secrets
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ParkShare_Backend.models import (
    AccountStatus,
    CodigoQR,
    QRCodeType,
    Reserva,
    ReservationStatus,
    UserRole,
    Usuario,
)
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    GoneError,
    NotFoundError,
    PermissionDeniedError,
)
from ParkShare_Backend.services.reserva_service import (
    TOLERANCE_MINUTES,
    calculate_late_surcharge,
    finish_reservation,
    start_reservation_usage,
)
from ParkShare_Backend.validators import SHA256_HEX_REGEX

EARLY_ENTRY_WINDOW_MINUTES = 15
EXIT_QR_VALIDITY_HOURS = 24


def generate_sha256_qr_token() -> str:
    """Generate a CSPRNG-backed 64-character lowercase SHA-256 hex token."""
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()


def calculate_late_checkout_penalty(
    scheduled_end: datetime,
    actual_exit: datetime,
) -> tuple[int, Decimal]:
    """Compute RN-02 grace period and RN-03 late checkout surcharge ($10 MXN/min)."""
    return calculate_late_surcharge(scheduled_end, actual_exit)


def apply_late_checkout_payment_adjustment_hook(reservation: Reserva) -> None:
    """Decoupled integration hook for Module 05 (`Pago`) late checkout surcharge adjustment."""
    return None


def _issue_qr_for_type(
    reservation: Reserva,
    qr_type: str,
    issued_at: datetime,
    expiration: datetime,
) -> CodigoQR:
    existing = CodigoQR.objects.filter(reserva=reservation, tipo_qr=qr_type).first()
    if existing is not None:
        if existing.usado:
            raise ConflictError(
                f'El código QR de {qr_type.lower()} ya fue utilizado previamente.',
                error_code='QRAlreadyUsed',
            )
        return existing

    return CodigoQR.objects.create(
        reserva=reservation,
        token_qr=generate_sha256_qr_token(),
        tipo_qr=qr_type,
        usado=False,
        fecha_emision=issued_at,
        expiracion=expiration,
    )


def issue_entry_qr(reserva: Reserva | int, now: datetime | None = None) -> CodigoQR:
    """Idempotently issue an ENTRADA SHA-256 QR token for a CONFIRMADA reservation (CU-06)."""
    reservation_id = reserva.id_reserva if isinstance(reserva, Reserva) else reserva
    issued_at = now or timezone.now()

    with transaction.atomic():
        locked_reservation = (
            Reserva.objects.select_for_update().filter(id_reserva=reservation_id).first()
        )
        if locked_reservation is None:
            raise NotFoundError('La reserva solicitada no existe.', error_code='NotFound')

        if locked_reservation.estado != ReservationStatus.CONFIRMADA:
            raise ConflictError(
                f'La reserva no cuenta con un código QR activo en su estado actual ({locked_reservation.estado}).',
                error_code='InvalidReservationState',
            )

        return _issue_qr_for_type(
            reservation=locked_reservation,
            qr_type=QRCodeType.ENTRADA,
            issued_at=issued_at,
            expiration=locked_reservation.fecha_hora_fin,
        )


def issue_exit_qr(reserva: Reserva, now: datetime | None = None) -> CodigoQR:
    """Idempotently issue a SALIDA QR token with +24h validity beyond end/now (RN-06)."""
    issued_at = now or timezone.now()
    expiration = max(reserva.fecha_hora_fin, issued_at) + timedelta(hours=EXIT_QR_VALIDITY_HOURS)
    return _issue_qr_for_type(
        reservation=reserva,
        qr_type=QRCodeType.SALIDA,
        issued_at=issued_at,
        expiration=expiration,
    )


def get_active_qr_for_reservation(reservation_id: int, tenant_user: Usuario) -> CodigoQR:
    """Return active ENTRADA or SALIDA QR for the owning tenant (GET /reservas/{id}/qr)."""
    if (
        not tenant_user
        or not getattr(tenant_user, 'is_authenticated', False)
        or tenant_user.rol != UserRole.ARRENDATARIO
        or tenant_user.estado_cuenta != AccountStatus.ACTIVA
    ):
        raise PermissionDeniedError(
            'No tienes permiso para consultar el código QR de esta reserva.',
            error_code='Forbidden',
        )

    reservation = (
        Reserva.objects.select_related('arrendatario', 'cochera')
        .prefetch_related('codigos_qr')
        .filter(id_reserva=reservation_id)
        .first()
    )
    if reservation is None:
        raise NotFoundError('La reserva solicitada no existe.', error_code='NotFound')

    if reservation.arrendatario_id != tenant_user.id_usuario:
        raise PermissionDeniedError(
            'No tienes permiso para consultar el código QR de esta reserva.',
            error_code='Forbidden',
        )

    if reservation.estado == ReservationStatus.CONFIRMADA:
        expected_type = QRCodeType.ENTRADA
    elif reservation.estado == ReservationStatus.EN_USO:
        expected_type = QRCodeType.SALIDA
    else:
        raise ConflictError(
            f'La reserva no cuenta con un código QR activo en su estado actual ({reservation.estado}).',
            error_code='InvalidReservationState',
        )

    active_qrs = [
        qr
        for qr in reservation.codigos_qr.all()
        if qr.tipo_qr == expected_type and not qr.usado
    ]
    if not active_qrs:
        raise ConflictError(
            f'La reserva no cuenta con un código QR activo en su estado actual ({reservation.estado}).',
            error_code='InvalidReservationState',
        )
    return active_qrs[0]


def _build_tenant_verification_dict(tenant: Usuario) -> dict:
    return {
        'id_usuario': tenant.id_usuario,
        'nombre_completo': tenant.nombre_completo,
        'placas': tenant.placas,
        'modelo_vehiculo': tenant.modelo_vehiculo,
    }


def validate_scanned_qr(
    token: str,
    host_user: Usuario,
    now: datetime | None = None,
) -> dict:
    """Atomically validate a scanned ENTRADA or SALIDA QR token for the garage owner (CU-08 / RN-06)."""
    if (
        not host_user
        or not getattr(host_user, 'is_authenticated', False)
        or host_user.rol != UserRole.ARRENDADOR
        or host_user.estado_cuenta != AccountStatus.ACTIVA
    ):
        raise PermissionDeniedError(
            'No tienes permiso para validar códigos QR de una cochera.',
            error_code='Forbidden',
        )

    if not isinstance(token, str) or not SHA256_HEX_REGEX.match(token.strip()):
        raise DomainValidationError(
            'El token QR debe ser una cadena hexadecimal válida de 64 caracteres.',
            errors={'token': ['El token QR debe ser una cadena hexadecimal válida de 64 caracteres.']},
        )

    normalized_token = token.strip().lower()
    reference_now = now or timezone.now()

    with transaction.atomic():
        qr = (
            CodigoQR.objects.select_related('reserva__cochera', 'reserva__arrendatario')
            .select_for_update()
            .filter(token_qr=normalized_token)
            .first()
        )
        if qr is None:
            raise NotFoundError('Código QR no encontrado o inválido.', error_code='NotFound')

        reservation = (
            Reserva.objects.select_related('cochera', 'arrendatario')
            .select_for_update()
            .get(pk=qr.reserva_id)
        )

        if reservation.cochera.arrendador_id != host_user.id_usuario:
            raise PermissionDeniedError(
                'No tienes permiso para validar códigos QR de una cochera que no te pertenece.',
                error_code='Forbidden',
            )

        if qr.usado:
            raise ConflictError(
                'Este código QR ya fue utilizado previamente.',
                error_code='QRAlreadyUsed',
            )

        if qr.tipo_qr == QRCodeType.ENTRADA:
            if reservation.estado != ReservationStatus.CONFIRMADA:
                raise ConflictError(
                    f'La reserva no se encuentra confirmada para ingreso (estado actual: {reservation.estado}).',
                    error_code='InvalidReservationState',
                )
            if reference_now > qr.expiracion:
                raise GoneError('El código QR ha expirado y ya no es válido.')
            earliest_entry = reservation.fecha_hora_inicio - timedelta(
                minutes=EARLY_ENTRY_WINDOW_MINUTES
            )
            if reference_now < earliest_entry:
                raise ConflictError(
                    'Aún es demasiado pronto para validar la llegada. El ingreso abre 15 minutos antes.',
                    error_code='InvalidReservationState',
                )

            qr.usado = True
            qr.save(update_fields=['usado'])
            updated_reservation = start_reservation_usage(
                reservation.id_reserva,
                actor=host_user,
                now=reference_now,
            )
            issue_exit_qr(updated_reservation, now=reference_now)

            return {
                'mensaje': 'Entrada autorizada. La reserva ahora se encuentra en uso.',
                'tipo_qr': QRCodeType.ENTRADA,
                'estado_reserva': updated_reservation.estado,
                'qr_salida_emitido': True,
                'verificacion': {
                    'reserva_id': updated_reservation.id_reserva,
                    'fecha_hora_inicio': updated_reservation.fecha_hora_inicio,
                    'fecha_hora_fin': updated_reservation.fecha_hora_fin,
                    'arrendatario': _build_tenant_verification_dict(updated_reservation.arrendatario),
                },
            }

        # Case qr.tipo_qr == QRCodeType.SALIDA (RN-06)
        if reservation.estado != ReservationStatus.EN_USO:
            raise ConflictError(
                f'La reserva no se encuentra en uso para confirmar salida (estado actual: {reservation.estado}).',
                error_code='InvalidReservationState',
            )
        if reference_now > qr.expiracion:
            raise GoneError('El código QR ha expirado y ya no es válido.')

        qr.usado = True
        qr.save(update_fields=['usado'])

        effective_checkout = max(reference_now, reservation.fecha_hora_inicio)
        updated_reservation = finish_reservation(
            reservation.id_reserva,
            actor=host_user,
            real_checkout_dt=effective_checkout,
            now=reference_now,
        )
        late_minutes, _ = calculate_late_checkout_penalty(
            updated_reservation.fecha_hora_fin,
            updated_reservation.hora_salida_real,
        )
        if updated_reservation.monto_penalizacion > Decimal('0.00'):
            apply_late_checkout_payment_adjustment_hook(updated_reservation)

        message = (
            'Salida confirmada. Reserva finalizada con recargo por retraso.'
            if late_minutes > 0
            else 'Salida confirmada. Reserva finalizada sin recargos.'
        )
        return {
            'mensaje': message,
            'tipo_qr': QRCodeType.SALIDA,
            'estado_reserva': updated_reservation.estado,
            'verificacion': {
                'reserva_id': updated_reservation.id_reserva,
                'fecha_hora_inicio': updated_reservation.fecha_hora_inicio,
                'fecha_hora_fin': updated_reservation.fecha_hora_fin,
                'hora_salida_real': updated_reservation.hora_salida_real,
                'tolerancia_minutos': TOLERANCE_MINUTES,
                'minutos_retraso': late_minutes,
                'monto_base': f'{updated_reservation.monto_base:.2f}',
                'monto_penalizacion': f'{updated_reservation.monto_penalizacion:.2f}',
                'monto_total': f'{updated_reservation.monto_total:.2f}',
                'arrendatario': _build_tenant_verification_dict(updated_reservation.arrendatario),
            },
        }
