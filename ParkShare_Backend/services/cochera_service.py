import math
from collections import defaultdict
from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import F, FloatField, Value
from django.db.models.functions import ACos, Cast, Cos, Greatest, Least, Radians, Sin
from django.utils import timezone

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    Disponibilidad,
    FotoCochera,
    GarageStatus,
    UserRole,
    Usuario,
    VehicleType,
    WeekdayChoice,
)
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
    _validation_error_to_dict,
)
from ParkShare_Backend.validators import validate_garage_photo_extension

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
VALID_ORDER_OPTIONS = ('cercania', 'precio', 'disponibilidad')
EARTH_RADIUS_KM = 6371.0
UPDATABLE_GARAGE_FIELDS = ('nombre', 'tarifa_hora', 'espacios_totales', 'tipo_vehiculo')


def _check_active_landlord(user: Usuario) -> None:
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.rol != UserRole.ARRENDADOR
        or user.estado_cuenta != AccountStatus.ACTIVA
    ):
        raise PermissionDeniedError(
            'Se requiere una cuenta de arrendador activa para realizar esta acción.'
        )


def _check_active_admin(user: Usuario) -> None:
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.rol != UserRole.ADMIN
        or user.estado_cuenta != AccountStatus.ACTIVA
    ):
        raise PermissionDeniedError('No tienes permisos de administrador para realizar esta acción.')


def _parse_time_value(raw_value, field_name: str) -> time:
    if isinstance(raw_value, time):
        return raw_value.replace(tzinfo=None)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise DomainValidationError(
            'Formato de hora inválido.',
            errors={field_name: ['Debe proporcionar una hora válida en formato HH:MM o HH:MM:SS.']},
        )
    cleaned = raw_value.strip()
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(cleaned, fmt).time()
        except ValueError:
            continue
    raise DomainValidationError(
        'Formato de hora inválido.',
        errors={field_name: ['Formato de hora inválido. Use HH:MM o HH:MM:SS.']},
    )


def validate_photo_urls(photo_urls) -> list[str]:
    """Validate that at least one valid garage image URL/path is provided (RN-09)."""
    if not isinstance(photo_urls, (list, tuple)) or len(photo_urls) == 0:
        raise DomainValidationError(
            'Debe cargar al menos una fotografía de la cochera.',
            errors={'fotos': ['Se requiere al menos una fotografía en formato JPG, PNG o WEBP.']},
        )

    cleaned_urls = []
    for idx, raw_url in enumerate(photo_urls):
        if not isinstance(raw_url, str) or not raw_url.strip():
            raise DomainValidationError(
                'URL o ruta de fotografía inválida.',
                errors={'fotos': [f'La fotografía #{idx + 1} no puede estar vacía.']},
            )
        cleaned = raw_url.strip()
        if len(cleaned) > 255:
            raise DomainValidationError(
                'La ruta de la fotografía excede la longitud máxima permitida.',
                errors={'fotos': [f'La fotografía #{idx + 1} excede 255 caracteres.']},
            )
        try:
            validate_garage_photo_extension(cleaned)
        except DjangoValidationError as exc:
            raise DomainValidationError(
                'Formato de fotografía no permitido.',
                errors={'fotos': exc.messages},
            ) from exc
        cleaned_urls.append(cleaned)

    return cleaned_urls


def validate_availability_slots(slots) -> list[dict]:
    """Validate weekly schedule windows and reject overlapping slots on the same weekday (CU-03)."""
    if not isinstance(slots, (list, tuple)) or len(slots) == 0:
        raise DomainValidationError(
            'Debe definir al menos una franja horaria de disponibilidad.',
            errors={'disponibilidades': ['Se requiere al menos una franja horaria válida.']},
        )

    cleaned_slots = []
    grouped_by_day = defaultdict(list)

    for raw_slot in slots:
        if not isinstance(raw_slot, dict):
            raise DomainValidationError(
                'Estructura de franja horaria inválida.',
                errors={'disponibilidades': ['Cada franja horaria debe ser un objeto válido.']},
            )

        raw_weekday = raw_slot.get('dia_semana')
        if not isinstance(raw_weekday, str):
            raise DomainValidationError(
                'Día de la semana inválido en disponibilidad.',
                errors={'disponibilidades': ['dia_semana debe ser una cadena de texto válida (L, M, X, J, V, S, D).']},
            )
        weekday = raw_weekday.strip().upper()
        if weekday not in WeekdayChoice.values:
            raise DomainValidationError(
                'Día de la semana inválido en disponibilidad.',
                errors={'disponibilidades': ['dia_semana debe ser uno de: L, M, X, J, V, S, D.']},
            )

        start_time = _parse_time_value(raw_slot.get('hora_inicio'), 'disponibilidades')
        end_time = _parse_time_value(raw_slot.get('hora_fin'), 'disponibilidades')

        if end_time <= start_time:
            raise DomainValidationError(
                'La hora de cierre debe ser posterior a la hora de apertura.',
                errors={'disponibilidades': ['La hora_fin debe ser estrictamente mayor que hora_inicio.']},
            )

        slot_data = {
            'dia_semana': weekday,
            'hora_inicio': start_time,
            'hora_fin': end_time,
        }
        cleaned_slots.append(slot_data)
        grouped_by_day[weekday].append(slot_data)

    for weekday, day_slots in grouped_by_day.items():
        day_slots_sorted = sorted(day_slots, key=lambda item: (item['hora_inicio'], item['hora_fin']))
        for index in range(1, len(day_slots_sorted)):
            previous_slot = day_slots_sorted[index - 1]
            current_slot = day_slots_sorted[index]
            if previous_slot['hora_fin'] > current_slot['hora_inicio']:
                raise DomainValidationError(
                    f'Existen franjas horarias traslapadas para el día {weekday}.',
                    errors={
                        'disponibilidades': [
                            f'Las franjas horarias del día {weekday} no deben superponerse.'
                        ]
                    },
                )

    return cleaned_slots


def get_active_reservations_count(garage: Cochera, reference_dt: datetime | None = None) -> int:
    """Decoupled integration hook for Module 03 (`reserva`) active space occupancy."""
    return 0


def get_garage_rating_summary(garage: Cochera) -> dict:
    """Decoupled integration hook for Module 06 (`resena`) rating summary."""
    return {
        'calificacion_promedio': None,
        'total_resenas': 0,
    }


def evaluate_garage_realtime_availability(
    garage: Cochera,
    current_dt: datetime | None = None,
) -> tuple[bool, int]:
    """Evaluate if a garage is open right now in Mexico City timezone and count free spaces."""
    local_dt = (current_dt or timezone.now()).astimezone(MEXICO_TZ)
    weekday_code = WEEKDAY_CODES[local_dt.weekday()]
    current_time = local_dt.time().replace(microsecond=0, tzinfo=None)

    is_open = any(
        slot.dia_semana == weekday_code and slot.hora_inicio <= current_time <= slot.hora_fin
        for slot in garage.disponibilidades.all()
    )
    if not is_open:
        return False, 0

    occupied = get_active_reservations_count(garage, local_dt)
    available_spaces = max(0, garage.espacios_totales - occupied)
    return True, available_spaces


def _fetch_garage_with_relations(garage_id: int) -> Cochera:
    return (
        Cochera.objects.select_related('arrendador')
        .prefetch_related('fotos', 'disponibilidades')
        .get(id_cochera=garage_id)
    )


def create_garage(landlord: Usuario, data: dict) -> Cochera:
    """Atomically create a garage listing with photos and schedule windows in PENDIENTE state."""
    _check_active_landlord(landlord)

    cleaned_photos = validate_photo_urls(data.get('fotos'))
    cleaned_slots = validate_availability_slots(data.get('disponibilidades'))

    raw_name = data.get('nombre')
    raw_address = data.get('direccion')
    raw_vehicle = data.get('tipo_vehiculo', VehicleType.AMBOS)
    garage = Cochera(
        arrendador=landlord,
        nombre=raw_name.strip() if isinstance(raw_name, str) else '',
        direccion=raw_address.strip() if isinstance(raw_address, str) else '',
        latitud=data.get('latitud'),
        longitud=data.get('longitud'),
        metros_cuadrados=data.get('metros_cuadrados'),
        espacios_totales=data.get('espacios_totales'),
        tarifa_hora=data.get('tarifa_hora'),
        tipo_vehiculo=(
            raw_vehicle.strip().upper()
            if isinstance(raw_vehicle, str) and raw_vehicle.strip()
            else (VehicleType.AMBOS if raw_vehicle is None else str(raw_vehicle))
        ),
        estado_cochera=GarageStatus.PENDIENTE,
    )

    try:
        garage.full_clean()
    except DjangoValidationError as exc:
        raise DomainValidationError(
            'Los datos de la cochera contienen errores de validación.',
            errors=_validation_error_to_dict(exc),
        ) from exc

    with transaction.atomic():
        garage.save()
        FotoCochera.objects.bulk_create(
            [FotoCochera(cochera=garage, url_foto=url) for url in cleaned_photos]
        )
        Disponibilidad.objects.bulk_create(
            [Disponibilidad(cochera=garage, **slot) for slot in cleaned_slots]
        )

    return _fetch_garage_with_relations(garage.id_cochera)


def _validate_owner_status_transition(current_status: str, target_status: str) -> None:
    if target_status not in GarageStatus.values:
        raise DomainValidationError(
            'Estado de cochera inválido.',
            errors={'estado_cochera': ['El estado indicado no es válido.']},
        )
    if target_status == current_status:
        return

    if target_status == GarageStatus.APROBADA and current_status in (
        GarageStatus.PENDIENTE,
        GarageStatus.RECHAZADA,
    ):
        raise PermissionDeniedError('Solo un administrador puede aprobar una cochera.')

    if target_status == GarageStatus.INACTIVA and current_status in (
        GarageStatus.PENDIENTE,
        GarageStatus.RECHAZADA,
    ):
        raise ConflictError('Solo una cochera aprobada puede pausarse o reactivarse.')

    allowed_pair = {
        (GarageStatus.APROBADA, GarageStatus.INACTIVA),
        (GarageStatus.INACTIVA, GarageStatus.APROBADA),
    }
    if (current_status, target_status) not in allowed_pair:
        raise PermissionDeniedError('No tienes permiso para asignar ese estado a la cochera.')


def update_garage(garage_id: int, landlord: Usuario, data: dict) -> Cochera:
    """Update garage attributes, atomic availability schedule, or APROBADA <-> INACTIVA toggle."""
    _check_active_landlord(landlord)

    has_slots = 'disponibilidades' in data
    cleaned_slots = validate_availability_slots(data['disponibilidades']) if has_slots else None

    with transaction.atomic():
        garage = (
            Cochera.objects.select_related('arrendador')
            .select_for_update()
            .filter(id_cochera=garage_id)
            .first()
        )
        if garage is None:
            raise NotFoundError('Cochera no encontrada.')

        if garage.arrendador_id != landlord.id_usuario:
            raise PermissionDeniedError('No tienes permiso para modificar esta cochera.')

        if 'estado_cochera' in data and data['estado_cochera'] is not None:
            target_status = str(data['estado_cochera']).strip().upper()
            _validate_owner_status_transition(garage.estado_cochera, target_status)
            garage.estado_cochera = target_status

        for field in UPDATABLE_GARAGE_FIELDS:
            if field in data and data[field] is not None:
                value = data[field]
                if field == 'tipo_vehiculo':
                    value = str(value).strip().upper()
                elif isinstance(value, str):
                    value = value.strip()
                setattr(garage, field, value)

        try:
            garage.full_clean()
        except DjangoValidationError as exc:
            raise DomainValidationError(
                'Los datos actualizados contienen errores de validación.',
                errors=_validation_error_to_dict(exc),
            ) from exc

        garage.save()

        if has_slots:
            Disponibilidad.objects.filter(cochera=garage).delete()
            Disponibilidad.objects.bulk_create(
                [Disponibilidad(cochera=garage, **slot) for slot in cleaned_slots]
            )

    return _fetch_garage_with_relations(garage.id_cochera)


def validate_garage_publication(
    garage_id: int,
    admin_user: Usuario,
    new_status: str,
    reason: str | None = None,
) -> dict:
    """Approve or reject a pending garage listing (RN-10)."""
    _check_active_admin(admin_user)

    normalized_status = (new_status or '').strip().upper()
    if normalized_status not in (GarageStatus.APROBADA, GarageStatus.RECHAZADA):
        raise DomainValidationError(
            'El estado de validación indicado no es válido.',
            errors={'estado_cochera': ['Debe ser APROBADA o RECHAZADA.']},
        )

    cleaned_reason = (reason or '').strip() or None
    if normalized_status == GarageStatus.RECHAZADA and not cleaned_reason:
        raise DomainValidationError(
            'El motivo es obligatorio al rechazar la publicación de una cochera.',
            errors={'motivo': ['Este campo es obligatorio al rechazar una cochera.']},
        )

    with transaction.atomic():
        garage = Cochera.objects.select_for_update().filter(id_cochera=garage_id).first()
        if garage is None:
            raise NotFoundError('Cochera no encontrada.')

        if garage.estado_cochera != GarageStatus.PENDIENTE:
            raise ConflictError('La cochera ya fue validada anteriormente.')

        garage.estado_cochera = normalized_status
        garage.save(update_fields=['estado_cochera'])

    message = (
        'Cochera aprobada y publicada exitosamente.'
        if normalized_status == GarageStatus.APROBADA
        else 'Cochera rechazada.'
    )
    return {
        'mensaje': message,
        'id_cochera': garage.id_cochera,
        'estado_cochera': garage.estado_cochera,
        'motivo': None if normalized_status == GarageStatus.APROBADA else cleaned_reason,
    }


def get_garage_detail_for_user(garage_id: int, user: Usuario) -> Cochera:
    """Return garage detail enforcing visibility rules (APROBADA public, others owner/admin only)."""
    garage = (
        Cochera.objects.select_related('arrendador')
        .prefetch_related('fotos', 'disponibilidades')
        .filter(id_cochera=garage_id)
        .first()
    )
    if garage is None:
        raise NotFoundError('Cochera no encontrada.')

    is_owner = getattr(user, 'id_usuario', None) == garage.arrendador_id
    is_admin = getattr(user, 'rol', None) == UserRole.ADMIN
    if garage.estado_cochera != GarageStatus.APROBADA and not (is_owner or is_admin):
        raise NotFoundError('Cochera no encontrada.')

    return garage


def list_landlord_garages(landlord: Usuario, status_filter: str | None = None) -> list[Cochera]:
    """List all garages owned by the authenticated active landlord."""
    _check_active_landlord(landlord)

    queryset = (
        Cochera.objects.filter(arrendador=landlord)
        .prefetch_related('fotos')
        .order_by('-id_cochera')
    )

    cleaned_status = (status_filter or '').strip().upper()
    if cleaned_status and cleaned_status != 'TODOS':
        if cleaned_status not in GarageStatus.values:
            raise DomainValidationError(
                'El estado de cochera indicado no es válido.',
                errors={'estado': ['Estado de cochera inválido.']},
            )
        queryset = queryset.filter(estado_cochera=cleaned_status)

    return list(queryset)


def _parse_float_param(raw_value, field_name: str, min_val: float, max_val: float, required: bool = True) -> float | None:
    if raw_value is None or raw_value == '':
        if required:
            raise DomainValidationError(
                f'El parámetro {field_name} es obligatorio.',
                errors={field_name: [f'El parámetro {field_name} es obligatorio.']},
            )
        return None
    if isinstance(raw_value, bool):
        raise DomainValidationError(
            f'El parámetro {field_name} debe ser numérico.',
            errors={field_name: [f'{field_name} debe ser un número válido.']},
        )
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(
            f'El parámetro {field_name} debe ser numérico.',
            errors={field_name: [f'{field_name} debe ser un número válido.']},
        ) from exc

    if math.isnan(parsed) or math.isinf(parsed) or parsed < min_val or parsed > max_val:
        raise DomainValidationError(
            f'El parámetro {field_name} está fuera del rango permitido.',
            errors={field_name: [f'{field_name} debe estar entre {min_val} y {max_val}.']},
        )
    return parsed


def _build_haversine_distance_expression(lat_float: float, lng_float: float):
    lat1_rad = Radians(Value(lat_float, output_field=FloatField()))
    lng1_rad = Radians(Value(lng_float, output_field=FloatField()))
    lat2_rad = Radians(Cast(F('latitud'), FloatField()))
    lng2_rad = Radians(Cast(F('longitud'), FloatField()))

    cosine_arg = (
        Cos(lat1_rad) * Cos(lat2_rad) * Cos(lng2_rad - lng1_rad)
        + Sin(lat1_rad) * Sin(lat2_rad)
    )
    clamped_arg = Greatest(
        Value(-1.0, output_field=FloatField()),
        Least(Value(1.0, output_field=FloatField()), cosine_arg),
    )
    return ACos(clamped_arg) * Value(EARTH_RADIUS_KM, output_field=FloatField())


def search_nearby_garages(
    lat,
    lng,
    order_by: str = 'cercania',
    radius_km=None,
    vehicle_type: str | None = None,
    current_dt: datetime | None = None,
) -> list[Cochera]:
    """Search approved garages near coordinates using Haversine SQL distance (CU-04)."""
    lat_float = _parse_float_param(lat, 'lat', -90.0, 90.0, required=True)
    lng_float = _parse_float_param(lng, 'lng', -180.0, 180.0, required=True)

    normalized_order = (order_by or 'cercania').strip().lower()
    if normalized_order not in VALID_ORDER_OPTIONS:
        raise DomainValidationError(
            'El criterio de ordenamiento no es válido.',
            errors={'orden': ['Debe ser cercania, precio o disponibilidad.']},
        )

    radius_float = None
    if radius_km is not None and radius_km != '':
        radius_float = _parse_float_param(radius_km, 'radio_km', 0.000001, 40075.0, required=False)

    queryset = (
        Cochera.objects.filter(estado_cochera=GarageStatus.APROBADA)
        .select_related('arrendador')
        .prefetch_related('fotos', 'disponibilidades')
        .annotate(distancia_km=_build_haversine_distance_expression(lat_float, lng_float))
    )

    if vehicle_type is not None and str(vehicle_type).strip() != '':
        normalized_vehicle = str(vehicle_type).strip().upper()
        if normalized_vehicle not in VehicleType.values:
            raise DomainValidationError(
                'El tipo de vehículo indicado no es válido.',
                errors={'tipo_vehiculo': ['Debe ser AUTO, MOTO o AMBOS.']},
            )
        if normalized_vehicle in (VehicleType.AUTO, VehicleType.MOTO):
            queryset = queryset.filter(tipo_vehiculo__in=[normalized_vehicle, VehicleType.AMBOS])
        elif normalized_vehicle == VehicleType.AMBOS:
            queryset = queryset.filter(tipo_vehiculo=VehicleType.AMBOS)

    if radius_float is not None:
        queryset = queryset.filter(distancia_km__lte=radius_float)

    garages = list(queryset)
    reference_dt = current_dt or timezone.now()

    for garage in garages:
        raw_dist = float(garage.distancia_km or 0.0)
        garage._raw_distance_km = raw_dist
        garage.distancia_km = round(raw_dist, 2)
        is_open, available_spaces = evaluate_garage_realtime_availability(garage, reference_dt)
        garage.abierta_ahora = is_open
        garage.espacios_disponibles_ahora = available_spaces

    if normalized_order == 'precio':
        garages.sort(key=lambda g: (g.tarifa_hora, g._raw_distance_km, g.id_cochera))
    elif normalized_order == 'disponibilidad':
        garages.sort(key=lambda g: (-g.espacios_disponibles_ahora, g._raw_distance_km, g.id_cochera))
    else:
        garages.sort(key=lambda g: (g._raw_distance_km, g.id_cochera))

    return garages
