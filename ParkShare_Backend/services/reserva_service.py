from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    GarageStatus,
    Reserva,
    ReservationStatus,
    UserRole,
    Usuario,
    WeekdayChoice,
)
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)

MEXICO_TZ = ZoneInfo('America/Mexico_City')
WEEKDAY_CODES = (
    WeekdayChoice.L,
    WeekdayChoice.M,
    WeekdayChoice.X,
    WeekdayChoice.J,
    WeekdayChoice.V,
    WeekdayChoice.S,
    WeekdayChoice.D,
)
ACTIVE_RESERVATION_STATUSES = (
    ReservationStatus.SOLICITADA,
    ReservationStatus.CONFIRMADA,
    ReservationStatus.EN_USO,
)
TERMINAL_RESERVATION_STATUSES = (
    ReservationStatus.FINALIZADA,
    ReservationStatus.CANCELADA,
    ReservationStatus.EXPIRADA,
)

MIN_DURATION_SECONDS = 1800
MAX_DURATION_SECONDS = 86400
DURATION_STEP_SECONDS = 1800
TOLERANCE_MINUTES = 10
LATE_FEE_PER_MINUTE = Decimal('10.00')
LATE_CANCEL_PENALTY = Decimal('100.00')
END_OF_DAY_MIN_TIME = time(23, 59, 0)

ALLOWED_TRANSITIONS = {
    (ReservationStatus.SOLICITADA, ReservationStatus.CONFIRMADA): {UserRole.ADMIN},
    (ReservationStatus.SOLICITADA, ReservationStatus.CANCELADA): {'TENANT_OWNER', UserRole.ADMIN},
    (ReservationStatus.CONFIRMADA, ReservationStatus.EN_USO): {'LANDLORD_OWNER', UserRole.ADMIN},
    (ReservationStatus.CONFIRMADA, ReservationStatus.CANCELADA): {'TENANT_OWNER', UserRole.ADMIN},
    (ReservationStatus.CONFIRMADA, ReservationStatus.EXPIRADA): {'LANDLORD_OWNER', UserRole.ADMIN},
    (ReservationStatus.EN_USO, ReservationStatus.FINALIZADA): {'LANDLORD_OWNER', UserRole.ADMIN},
}

TENANT_VIEW_CONFIG = {
    'proximas': (ACTIVE_RESERVATION_STATUSES, ('fecha_hora_inicio', 'id_reserva')),
    'anteriores': (TERMINAL_RESERVATION_STATUSES, ('-fecha_hora_inicio', '-id_reserva')),
}

LANDLORD_VIEW_CONFIG = {
    'proximas_llegadas': ((ReservationStatus.CONFIRMADA,), ('fecha_hora_inicio', 'id_reserva')),
    'en_uso': ((ReservationStatus.EN_USO,), ('fecha_hora_fin', 'id_reserva')),
    'historial': (TERMINAL_RESERVATION_STATUSES, ('-fecha_hora_inicio', '-id_reserva')),
}


def _ensure_aware_dt(dt: datetime) -> datetime:
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, MEXICO_TZ)
    return dt


def _check_active_tenant(user: Usuario) -> None:
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.rol != UserRole.ARRENDATARIO
        or user.estado_cuenta != AccountStatus.ACTIVA
    ):
        raise PermissionDeniedError(
            'Se requiere una cuenta de arrendatario activa para realizar esta acción.'
        )


def validate_reservation_duration(start_dt: datetime, end_dt: datetime) -> int:
    """Validate RN-01: 30 minutes to 24 hours in exact 30-minute increments."""
    aware_start = _ensure_aware_dt(start_dt)
    aware_end = _ensure_aware_dt(end_dt)

    if aware_end <= aware_start:
        raise DomainValidationError(
            'La fecha y hora de fin debe ser posterior a la de inicio.',
            errors={'fecha_hora_fin': ['Debe ser posterior a fecha_hora_inicio.']},
        )

    delta = aware_end - aware_start
    total_seconds = delta.days * 86400 + delta.seconds
    if (
        delta.microseconds != 0
        or total_seconds < MIN_DURATION_SECONDS
        or total_seconds > MAX_DURATION_SECONDS
        or total_seconds % DURATION_STEP_SECONDS != 0
    ):
        raise DomainValidationError(
            'La duración debe ser entre 30 minutos y 24 horas en intervalos exactos de 30 minutos.',
            errors={
                'fecha_hora_fin': [
                    'El tiempo reservado debe ser múltiplo de 30 minutos (mínimo 30 min, máximo 24 h).'
                ]
            },
        )

    return total_seconds // 60


def calculate_base_amount(hourly_rate: Decimal, duration_minutes: int) -> Decimal:
    """Calculate base reservation amount rounded to 2 decimal places."""
    raw = Decimal(hourly_rate) * Decimal(duration_minutes) / Decimal('60')
    return raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _ceil_minutes(delta: timedelta) -> int:
    total_micros = delta.days * 86400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    if total_micros <= 0:
        return 0
    return (total_micros + 60_000_000 - 1) // 60_000_000


def calculate_late_surcharge(end_dt: datetime, checkout_dt: datetime) -> tuple[int, Decimal]:
    """Calculate RN-02 (10 min grace period) and RN-03 ($10 MXN/started minute beyond grace)."""
    tolerance_limit = _ensure_aware_dt(end_dt) + timedelta(minutes=TOLERANCE_MINUTES)
    aware_checkout = _ensure_aware_dt(checkout_dt)

    if aware_checkout <= tolerance_limit:
        return 0, Decimal('0.00')

    late_minutes = _ceil_minutes(aware_checkout - tolerance_limit)
    surcharge = (Decimal(late_minutes) * LATE_FEE_PER_MINUTE).quantize(Decimal('0.01'))
    return late_minutes, surcharge


def calculate_cancellation_penalty(
    reservation_status: str,
    created_dt: datetime,
    cancel_dt: datetime,
) -> tuple[bool, Decimal]:
    """Calculate RN-04 (free <= 10 min) and RN-05 ($100 MXN after 10 min on CONFIRMADA)."""
    if reservation_status == ReservationStatus.SOLICITADA:
        return False, Decimal('0.00')

    free_limit = _ensure_aware_dt(created_dt) + timedelta(minutes=TOLERANCE_MINUTES)
    if _ensure_aware_dt(cancel_dt) <= free_limit:
        return False, Decimal('0.00')

    return True, LATE_CANCEL_PENALTY


def _merged_day_slots(garage: Cochera, weekday_code: str) -> list[tuple[time, time]]:
    day_slots = sorted(
        (
            (slot.hora_inicio, slot.hora_fin)
            for slot in garage.disponibilidades.all()
            if slot.dia_semana == weekday_code
        ),
        key=lambda item: (item[0], item[1]),
    )
    merged: list[tuple[time, time]] = []
    for start_t, end_t in day_slots:
        if merged and merged[-1][1] == start_t:
            merged[-1] = (merged[-1][0], end_t)
        else:
            merged.append((start_t, end_t))
    return merged


def _day_covers_range(slots: list[tuple[time, time]], req_start: time, req_end: time) -> bool:
    for slot_start, slot_end in slots:
        end_ok = (
            slot_end >= END_OF_DAY_MIN_TIME
            if req_end >= END_OF_DAY_MIN_TIME
            else slot_end >= req_end
        )
        if slot_start <= req_start and end_ok:
            return True
    return False


def validate_schedule_coverage(garage: Cochera, start_dt: datetime, end_dt: datetime) -> None:
    """Validate that [start_dt, end_dt] fits inside the garage weekly availability slots."""
    local_start = _ensure_aware_dt(start_dt).astimezone(MEXICO_TZ)
    local_end = _ensure_aware_dt(end_dt).astimezone(MEXICO_TZ)
    start_time = local_start.time().replace(tzinfo=None)
    end_time = local_end.time().replace(tzinfo=None)

    start_day_code = WEEKDAY_CODES[local_start.weekday()]
    start_day_slots = _merged_day_slots(garage, start_day_code)

    if local_start.date() == local_end.date():
        covered = _day_covers_range(start_day_slots, start_time, end_time)
    elif local_end.date() == local_start.date() + timedelta(days=1):
        covered = _day_covers_range(start_day_slots, start_time, END_OF_DAY_MIN_TIME)
        if covered and end_time > time(0, 0, 0):
            next_day_code = WEEKDAY_CODES[local_end.weekday()]
            next_day_slots = _merged_day_slots(garage, next_day_code)
            covered = _day_covers_range(next_day_slots, time(0, 0, 0), end_time)
    else:
        covered = False

    if not covered:
        raise DomainValidationError(
            'El horario solicitado está fuera de la disponibilidad de la cochera.',
            errors={'fecha_hora_inicio': ['La cochera no opera en todo el rango horario solicitado.']},
        )


def create_reservation(
    tenant: Usuario,
    garage_id: int,
    start_dt: datetime,
    end_dt: datetime,
    now: datetime | None = None,
) -> Reserva:
    """Create a reservation atomically enforcing RN-01, schedule coverage, and locked capacity (CU-05)."""
    _check_active_tenant(tenant)

    aware_start = _ensure_aware_dt(start_dt)
    aware_end = _ensure_aware_dt(end_dt)
    duration_minutes = validate_reservation_duration(aware_start, aware_end)

    reference_now = _ensure_aware_dt(now) if now is not None else timezone.now()
    if aware_start < reference_now:
        raise DomainValidationError(
            'No puedes realizar una reservación en el pasado.',
            errors={'fecha_hora_inicio': ['La fecha y hora de inicio debe ser actual o futura.']},
        )

    with transaction.atomic():
        garage = (
            Cochera.objects.select_for_update()
            .prefetch_related('disponibilidades')
            .filter(id_cochera=garage_id)
            .first()
        )
        if garage is None:
            raise NotFoundError('Cochera no encontrada.')

        if garage.arrendador_id == tenant.id_usuario:
            raise DomainValidationError(
                'No puedes reservar tu propia cochera.',
                errors={'cochera_id': ['El propietario no puede reservar su propia cochera.']},
            )

        if garage.estado_cochera != GarageStatus.APROBADA:
            raise ConflictError('La cochera no está disponible para reservaciones en su estado actual.')

        validate_schedule_coverage(garage, aware_start, aware_end)

        overlapping_count = Reserva.objects.filter(
            cochera=garage,
            estado__in=ACTIVE_RESERVATION_STATUSES,
            fecha_hora_inicio__lt=aware_end,
            fecha_hora_fin__gt=aware_start,
        ).count()
        if overlapping_count >= garage.espacios_totales:
            raise ConflictError('No hay espacios disponibles para el horario seleccionado.')

        base_amount = calculate_base_amount(garage.tarifa_hora, duration_minutes)
        return Reserva.objects.create(
            arrendatario=tenant,
            cochera=garage,
            fecha_hora_inicio=aware_start,
            fecha_hora_fin=aware_end,
            monto_base=base_amount,
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.SOLICITADA,
        )


def _actor_has_transition_permission(
    reservation: Reserva,
    actor: Usuario | None,
    allowed_principals: set[str],
    by_system: bool,
) -> tuple[bool, bool]:
    """Return (is_participant_or_admin, is_allowed_for_edge)."""
    if by_system:
        return True, True
    if not actor or not getattr(actor, 'is_authenticated', False):
        return False, False
    if actor.estado_cuenta != AccountStatus.ACTIVA:
        return False, False

    is_admin = actor.rol == UserRole.ADMIN
    is_tenant_owner = actor.id_usuario == reservation.arrendatario_id
    is_landlord_owner = actor.id_usuario == reservation.cochera.arrendador_id

    if not (is_admin or is_tenant_owner or is_landlord_owner):
        return False, False

    if is_admin and UserRole.ADMIN in allowed_principals:
        return True, True
    if is_tenant_owner and 'TENANT_OWNER' in allowed_principals:
        return True, True
    if is_landlord_owner and 'LANDLORD_OWNER' in allowed_principals:
        return True, True
    return True, False


def _execute_transition(
    reservation_id: int,
    target_status: str,
    actor: Usuario | None = None,
    real_checkout_dt: datetime | None = None,
    now: datetime | None = None,
    by_system: bool = False,
) -> tuple[Reserva, bool]:
    if target_status not in ReservationStatus.values:
        raise DomainValidationError(
            'El estado de reservación indicado no es válido.',
            errors={'estado': ['Estado de reservación desconocido.']},
        )

    reference_now = _ensure_aware_dt(now) if now is not None else timezone.now()
    late_cancel_applied = False

    with transaction.atomic():
        reservation = (
            Reserva.objects.select_related('cochera', 'arrendatario')
            .select_for_update()
            .filter(id_reserva=reservation_id)
            .first()
        )
        if reservation is None:
            raise NotFoundError('Reservación no encontrada.')

        edge = (reservation.estado, target_status)
        allowed_principals = ALLOWED_TRANSITIONS.get(edge)

        is_related, is_allowed_edge = _actor_has_transition_permission(
            reservation,
            actor,
            allowed_principals or set(),
            by_system,
        )
        if not is_related:
            raise PermissionDeniedError('No tienes permiso para acceder o modificar esta reservación.')

        if allowed_principals is None:
            raise ConflictError(
                f'No es posible cambiar una reservación de {reservation.estado} a {target_status}.'
            )

        if not is_allowed_edge:
            raise PermissionDeniedError(
                f'Tu rol no tiene autorización para cambiar la reservación a {target_status}.'
            )

        update_fields = ['estado']
        if target_status == ReservationStatus.EN_USO:
            if reference_now > reservation.fecha_hora_fin:
                raise ConflictError('La ventana de llegada para esta reservación ha expirado.')
        elif target_status == ReservationStatus.EXPIRADA:
            if reference_now <= reservation.fecha_hora_fin:
                raise ConflictError(
                    'No se puede marcar como expirada una reservación antes de su hora de término.'
                )
        elif target_status == ReservationStatus.CANCELADA:
            late_cancel_applied, penalty = calculate_cancellation_penalty(
                reservation.estado,
                reservation.fecha_creacion,
                reference_now,
            )
            reservation.monto_penalizacion = penalty
            update_fields.append('monto_penalizacion')
        elif target_status == ReservationStatus.FINALIZADA:
            checkout_dt = (
                _ensure_aware_dt(real_checkout_dt)
                if real_checkout_dt is not None
                else reference_now
            )
            if checkout_dt < reservation.fecha_hora_inicio:
                raise DomainValidationError(
                    'La hora de salida real no puede ser anterior al inicio de la reservación.',
                    errors={
                        'hora_salida_real': [
                            'Debe ser igual o posterior a fecha_hora_inicio.'
                        ]
                    },
                )
            _, surcharge = calculate_late_surcharge(reservation.fecha_hora_fin, checkout_dt)
            reservation.hora_salida_real = checkout_dt
            reservation.monto_penalizacion = surcharge
            update_fields.extend(['hora_salida_real', 'monto_penalizacion'])

        reservation.estado = target_status
        reservation.save(update_fields=update_fields)

        if target_status == ReservationStatus.CONFIRMADA:
            from ParkShare_Backend.services.qr_service import issue_entry_qr

            issue_entry_qr(reservation, now=reference_now)

    return reservation, late_cancel_applied


def transition_reservation_state(
    reservation_id: int,
    actor: Usuario | None,
    target_status: str,
    real_checkout_dt: datetime | None = None,
    now: datetime | None = None,
    by_system: bool = False,
) -> Reserva:
    """Validate role permissions, state-machine edges, and apply side effects atomically."""
    normalized_target = (target_status or '').strip().upper()
    reservation, _ = _execute_transition(
        reservation_id=reservation_id,
        target_status=normalized_target,
        actor=actor,
        real_checkout_dt=real_checkout_dt,
        now=now,
        by_system=by_system,
    )
    return reservation


def cancel_reservation(
    reservation_id: int,
    actor: Usuario | None = None,
    now: datetime | None = None,
    by_system: bool = False,
) -> tuple[Reserva, bool]:
    """Cancel a SOLICITADA or CONFIRMADA reservation applying RN-04 / RN-05."""
    return _execute_transition(
        reservation_id=reservation_id,
        target_status=ReservationStatus.CANCELADA,
        actor=actor,
        now=now,
        by_system=by_system,
    )


def confirm_reservation(
    reservation_id: int,
    actor: Usuario | None = None,
    now: datetime | None = None,
    by_system: bool = False,
) -> Reserva:
    """Transition SOLICITADA -> CONFIRMADA and issue entry QR (entrypoint for Module 05)."""
    reservation, _ = _execute_transition(
        reservation_id=reservation_id,
        target_status=ReservationStatus.CONFIRMADA,
        actor=actor,
        now=now,
        by_system=by_system,
    )
    return reservation


def start_reservation_usage(
    reservation_id: int,
    actor: Usuario | None = None,
    now: datetime | None = None,
    by_system: bool = False,
) -> Reserva:
    """Transition CONFIRMADA -> EN_USO (integration entrypoint for Module 04 entry QR)."""
    reservation, _ = _execute_transition(
        reservation_id=reservation_id,
        target_status=ReservationStatus.EN_USO,
        actor=actor,
        now=now,
        by_system=by_system,
    )
    return reservation


def finish_reservation(
    reservation_id: int,
    actor: Usuario | None = None,
    real_checkout_dt: datetime | None = None,
    now: datetime | None = None,
    by_system: bool = False,
) -> Reserva:
    """Transition EN_USO -> FINALIZADA applying RN-02 / RN-03 (entrypoint for Module 04 exit QR)."""
    reservation, _ = _execute_transition(
        reservation_id=reservation_id,
        target_status=ReservationStatus.FINALIZADA,
        actor=actor,
        real_checkout_dt=real_checkout_dt,
        now=now,
        by_system=by_system,
    )
    return reservation


def list_reservations_for_user(
    user: Usuario,
    view_filter: str | None = None,
    status_filter: str | None = None,
) -> list[Reserva]:
    """List reservations segmented by active tenant or landlord views."""
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.estado_cuenta != AccountStatus.ACTIVA
        or user.rol not in (UserRole.ARRENDATARIO, UserRole.ARRENDADOR)
    ):
        raise PermissionDeniedError(
            'Se requiere una cuenta activa de arrendatario o arrendador para consultar reservaciones.'
        )

    if user.rol == UserRole.ARRENDATARIO:
        queryset = Reserva.objects.filter(arrendatario=user).select_related('cochera', 'arrendatario')
        view_config = TENANT_VIEW_CONFIG
    else:
        queryset = Reserva.objects.filter(cochera__arrendador=user).select_related(
            'cochera', 'arrendatario'
        )
        view_config = LANDLORD_VIEW_CONFIG

    ordering = ('-fecha_hora_inicio', '-id_reserva')
    cleaned_view = (view_filter or '').strip().lower()
    if cleaned_view:
        if cleaned_view not in view_config:
            raise DomainValidationError(
                'El parámetro vista no es válido para tu rol.',
                errors={
                    'vista': [
                        f'Opciones válidas para {user.rol}: {", ".join(view_config.keys())}.'
                    ]
                },
            )
        statuses, ordering = view_config[cleaned_view]
        queryset = queryset.filter(estado__in=statuses)

    cleaned_status = (status_filter or '').strip().upper()
    if cleaned_status:
        if cleaned_status not in ReservationStatus.values:
            raise DomainValidationError(
                'El estado de reservación indicado no es válido.',
                errors={'estado': ['Estado de reservación inválido.']},
            )
        queryset = queryset.filter(estado=cleaned_status)

    return list(queryset.order_by(*ordering))


def build_reservation_live_metrics(
    reservation: Reserva,
    reference_now: datetime | None = None,
) -> dict:
    """Compute live usage timer, tolerance flag, and estimated late surcharge for GET /reservas/{id}."""
    now_dt = _ensure_aware_dt(reference_now) if reference_now is not None else timezone.now()

    if reservation.estado != ReservationStatus.EN_USO:
        return {
            'desglose_montos': {
                'monto_base': reservation.monto_base,
                'monto_penalizacion': reservation.monto_penalizacion,
                'monto_total': reservation.monto_total,
                'recargo_estimado_actual': Decimal('0.00'),
            },
            'tiempo_en_uso': None,
        }

    if now_dt < reservation.fecha_hora_fin:
        remaining_minutes = _ceil_minutes(reservation.fecha_hora_fin - now_dt)
        return {
            'desglose_montos': {
                'monto_base': reservation.monto_base,
                'monto_penalizacion': reservation.monto_penalizacion,
                'monto_total': reservation.monto_total,
                'recargo_estimado_actual': Decimal('0.00'),
            },
            'tiempo_en_uso': {
                'tiempo_restante_minutos': remaining_minutes,
                'en_tolerancia': False,
                'minutos_retraso_actual': 0,
            },
        }

    tolerance_limit = reservation.fecha_hora_fin + timedelta(minutes=TOLERANCE_MINUTES)
    in_tolerance = now_dt <= tolerance_limit
    late_minutes, estimated_surcharge = calculate_late_surcharge(reservation.fecha_hora_fin, now_dt)

    return {
        'desglose_montos': {
            'monto_base': reservation.monto_base,
            'monto_penalizacion': reservation.monto_penalizacion,
            'monto_total': reservation.monto_total,
            'recargo_estimado_actual': estimated_surcharge,
        },
        'tiempo_en_uso': {
            'tiempo_restante_minutos': 0,
            'en_tolerancia': in_tolerance,
            'minutos_retraso_actual': late_minutes,
        },
    }


def get_reservation_detail_for_user(
    reservation_id: int,
    user: Usuario,
    now: datetime | None = None,
) -> tuple[Reserva, dict]:
    """Retrieve a reservation with live metrics for owner tenant, garage landlord, or ADMIN."""
    reservation = (
        Reserva.objects.select_related('cochera', 'arrendatario')
        .filter(id_reserva=reservation_id)
        .first()
    )
    if reservation is None:
        raise NotFoundError('Reservación no encontrada.')

    is_admin = getattr(user, 'rol', None) == UserRole.ADMIN and getattr(user, 'estado_cuenta', None) == AccountStatus.ACTIVA
    is_tenant = getattr(user, 'id_usuario', None) == reservation.arrendatario_id
    is_landlord = getattr(user, 'id_usuario', None) == reservation.cochera.arrendador_id
    if not (is_admin or is_tenant or is_landlord):
        raise PermissionDeniedError('No tienes permiso para consultar esta reservación.')

    metrics = build_reservation_live_metrics(reservation, reference_now=now)
    return reservation, metrics
