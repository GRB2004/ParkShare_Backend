from collections import defaultdict
from datetime import datetime, time
from difflib import SequenceMatcher
import math
import re
import unicodedata
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, F, FloatField, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import ACos, Cast, Coalesce, Cos, Greatest, Least, Radians, Sin
from django.utils import timezone

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    Disponibilidad,
    FotoCochera,
    GarageStatus,
    Reserva,
    ReservationStatus,
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
ACTIVE_RESERVATION_STATUSES = (
    ReservationStatus.SOLICITADA,
    ReservationStatus.CONFIRMADA,
    ReservationStatus.EN_USO,
)
RECOMMENDATION_SIMILARITY_THRESHOLD = 0.42
SEARCH_STOP_WORDS = frozenset({
    'de', 'del', 'la', 'las', 'el', 'los', 'en', 'y', 'a', 'al', 'con', 'por', 'para',
})
STREET_ABBREVIATION_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r'\bav\.?\b'), 'avenida'),
    (re.compile(r'\bblvd\.?\b'), 'bulevar'),
    (re.compile(r'\bcalz\.?\b'), 'calzada'),
    (re.compile(r'\bcol\.?\b'), 'colonia'),
    (re.compile(r'\bfracc\.?\b'), 'fraccionamiento'),
    (re.compile(r'\bpriv\.?\b'), 'privada'),
    (re.compile(r'\b(?:num|no)\.?\b'), ' '),
)


def _normalize_search_text(raw_text: str | None) -> str:
    if not raw_text or not isinstance(raw_text, str):
        return ''
    lowered = (
        unicodedata.normalize('NFKD', raw_text.strip().lower())
        .encode('ascii', 'ignore')
        .decode('ascii')
    )
    for pattern, replacement in STREET_ABBREVIATION_RULES:
        lowered = pattern.sub(replacement, lowered)
    cleaned = re.sub(r'[^a-z0-9\s]', ' ', lowered)
    return re.sub(r'\s+', ' ', cleaned).strip()


def _phonetic_spanish_key(token: str) -> str:
    if not token:
        return ''
    s = token.lower()
    s = s.replace('ce', 'se').replace('ci', 'si').replace('qu', 'k').replace('ll', 'y')
    s = s.replace('z', 's').replace('v', 'b')
    if s.startswith('h') and len(s) > 1:
        s = s[1:]
    return s


def _tokenize_search_text(normalized_text: str) -> list[str]:
    if not normalized_text:
        return []
    raw_tokens = [tok for tok in normalized_text.split(' ') if tok]
    filtered = [tok for tok in raw_tokens if tok not in SEARCH_STOP_WORDS]
    return filtered if filtered else raw_tokens


def _score_single_query_token(qt: str, target_tokens: list[str], boost: float = 0.0) -> float:
    if not qt or not target_tokens:
        return 0.0
    qt_phon = _phonetic_spanish_key(qt)
    qt_is_num = qt.isdigit()
    best = 0.0

    for tt in target_tokens:
        score = 0.0
        if qt == tt:
            score = 1.0
        elif qt_is_num and tt.isdigit() and len(qt) == len(tt):
            diff = abs(int(qt) - int(tt))
            if diff <= 10:
                score = 0.90
            elif diff <= 50:
                score = 0.82
        elif not qt_is_num and not tt.isdigit():
            tt_phon = _phonetic_spanish_key(tt)
            if len(qt) >= 4 and qt_phon == tt_phon:
                score = 0.92
            elif len(qt) >= 3 and len(tt) >= 3 and (tt.startswith(qt) or qt.startswith(tt)):
                score = 0.88
            else:
                raw_ratio = SequenceMatcher(None, qt, tt).ratio()
                phon_ratio = SequenceMatcher(None, qt_phon, tt_phon).ratio()
                combined_ratio = max(raw_ratio, phon_ratio * 0.96)
                if combined_ratio >= 0.74:
                    score = combined_ratio * 0.90

        if score > 0.0:
            best = max(best, min(1.0, score + boost))
            if best >= 1.0:
                break

    return best


def compute_garage_search_similarity(
    query: str | None,
    name: str | None,
    address: str | None,
) -> tuple[float, bool]:
    """Compute similarity score [0.0..1.0] and recommendation flag against garage name & address."""
    norm_q = _normalize_search_text(query)
    if not norm_q:
        return 0.0, False

    norm_name = _normalize_search_text(name)
    norm_addr = _normalize_search_text(address)

    phrase_score = 0.0
    in_name = norm_q in norm_name if norm_name else False
    in_addr = norm_q in norm_addr if norm_addr else False
    if in_name and in_addr:
        phrase_score = 0.99
    elif in_name:
        phrase_score = 0.95
    elif in_addr:
        phrase_score = 0.91

    q_tokens = _tokenize_search_text(norm_q)
    name_tokens = _tokenize_search_text(norm_name)
    addr_tokens = _tokenize_search_text(norm_addr)

    token_score = 0.0
    if q_tokens:
        name_scores = [_score_single_query_token(qt, name_tokens) for qt in q_tokens]
        addr_scores = [_score_single_query_token(qt, addr_tokens) for qt in q_tokens]
        per_token = [
            min(
                0.98,
                max(ns * 0.94, ads * 0.89) + (0.05 if ns >= 0.75 and ads >= 0.75 else 0.0),
            )
            for ns, ads in zip(name_scores, addr_scores)
        ]
        strong_hits = sum(1 for s in per_token if s >= 0.72)
        token_score = sum(per_token) / len(q_tokens)
        if len(q_tokens) >= 2 and strong_hits / len(q_tokens) >= 0.75:
            token_score = min(0.96, token_score + 0.06)

    segment_score = 0.0
    segments = [norm_name] + [
        _normalize_search_text(seg) for seg in (address or '').split(',') if seg.strip()
    ]
    for seg in segments:
        if not seg:
            continue
        ratio = SequenceMatcher(None, norm_q, seg).ratio()
        if ratio >= 0.68:
            segment_score = max(segment_score, ratio * 0.85)

    final_score = round(min(1.0, max(phrase_score, token_score, segment_score)), 3)
    return final_score, final_score >= RECOMMENDATION_SIMILARITY_THRESHOLD


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


def _active_occupancy_q(check_dt: datetime) -> Q:
    """Match any EN_USO stay plus SOLICITADA/CONFIRMADA reservations overlapping check_dt."""
    return Q(estado=ReservationStatus.EN_USO) | Q(
        estado__in=(ReservationStatus.SOLICITADA, ReservationStatus.CONFIRMADA),
        fecha_hora_inicio__lte=check_dt,
        fecha_hora_fin__gt=check_dt,
    )


def get_active_reservations_count(garage: Cochera, reference_dt: datetime | None = None) -> int:
    """Count active reservations currently occupying garage spaces (Module 03)."""
    check_dt = reference_dt or timezone.now()
    annotated_count = getattr(garage, '_active_reservations_now', None)
    annotated_ref = getattr(garage, '_active_reservations_ref_dt', None)
    if annotated_count is not None and (reference_dt is None or annotated_ref == check_dt):
        return int(annotated_count)
    return Reserva.objects.filter(cochera=garage).filter(_active_occupancy_q(check_dt)).count()


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


def _build_active_reservations_subquery(reference_dt: datetime):
    return (
        Reserva.objects.filter(cochera_id=OuterRef('pk'))
        .filter(_active_occupancy_q(reference_dt))
        .order_by()
        .values('cochera_id')
        .annotate(active_count=Count('id_reserva'))
        .values('active_count')[:1]
    )


def _fetch_garage_with_relations(garage_id: int) -> Cochera:
    reference_dt = timezone.now()
    garage = (
        Cochera.objects.select_related('arrendador')
        .prefetch_related('fotos', 'disponibilidades')
        .annotate(
            _active_reservations_now=Coalesce(
                Subquery(_build_active_reservations_subquery(reference_dt), output_field=IntegerField()),
                Value(0),
            ),
        )
        .get(id_cochera=garage_id)
    )
    garage._active_reservations_ref_dt = reference_dt
    return garage


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
    reference_dt = timezone.now()
    garage = (
        Cochera.objects.select_related('arrendador')
        .prefetch_related('fotos', 'disponibilidades')
        .annotate(
            _active_reservations_now=Coalesce(
                Subquery(_build_active_reservations_subquery(reference_dt), output_field=IntegerField()),
                Value(0),
            ),
        )
        .filter(id_cochera=garage_id)
        .first()
    )
    if garage is None:
        raise NotFoundError('Cochera no encontrada.')

    is_owner = getattr(user, 'id_usuario', None) == garage.arrendador_id
    is_admin = getattr(user, 'rol', None) == UserRole.ADMIN
    if garage.estado_cochera != GarageStatus.APROBADA and not (is_owner or is_admin):
        raise NotFoundError('Cochera no encontrada.')

    garage._active_reservations_ref_dt = reference_dt
    return garage


def _filter_queryset_by_status(queryset, status_filter: str | None, default_status: str | None = None):
    raw_status = (status_filter or '').strip().upper() or default_status
    if raw_status and raw_status != 'TODOS':
        if raw_status not in GarageStatus.values:
            raise DomainValidationError(
                'El estado de cochera indicado no es válido.',
                errors={'estado': ['Estado de cochera inválido.']},
            )
        return queryset.filter(estado_cochera=raw_status)
    return queryset


def list_landlord_garages(landlord: Usuario, status_filter: str | None = None) -> list[Cochera]:
    """List all garages owned by the authenticated active landlord."""
    _check_active_landlord(landlord)

    reference_dt = timezone.now()
    queryset = (
        Cochera.objects.filter(arrendador=landlord)
        .prefetch_related('fotos', 'disponibilidades')
        .annotate(
            _active_reservations_now=Coalesce(
                Subquery(_build_active_reservations_subquery(reference_dt), output_field=IntegerField()),
                Value(0),
            ),
        )
        .order_by('-id_cochera')
    )
    queryset = _filter_queryset_by_status(queryset, status_filter)
    garages = list(queryset)
    for item in garages:
        item._active_reservations_ref_dt = reference_dt
    return garages


def list_admin_garages(admin_user: Usuario, status_filter: str | None = None) -> list[Cochera]:
    """List garages for administrative review defaulting to PENDIENTE (RN-10)."""
    _check_active_admin(admin_user)

    queryset = (
        Cochera.objects.select_related('arrendador')
        .prefetch_related('fotos', 'disponibilidades')
        .order_by('-id_cochera')
    )
    queryset = _filter_queryset_by_status(
        queryset,
        status_filter,
        default_status=GarageStatus.PENDIENTE,
    )
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
    lat=None,
    lng=None,
    order_by: str = 'cercania',
    radius_km=None,
    vehicle_type: str | None = None,
    search_query: str | None = None,
    current_dt: datetime | None = None,
) -> list[Cochera]:
    """Search approved garages near coordinates and/or prioritize similar names & addresses (CU-04)."""
    cleaned_query = (search_query or '').strip() if isinstance(search_query, str) else ''
    has_query = len(cleaned_query) > 0
    coords_required = not has_query

    lat_float = _parse_float_param(lat, 'lat', -90.0, 90.0, required=coords_required)
    lng_float = _parse_float_param(lng, 'lng', -180.0, 180.0, required=coords_required)
    if (lat_float is None) != (lng_float is None):
        missing_field = 'lat' if lat_float is None else 'lng'
        raise DomainValidationError(
            f'El parámetro {missing_field} es obligatorio cuando se envía una coordenada.',
            errors={missing_field: [f'El parámetro {missing_field} es obligatorio.']},
        )

    normalized_order = (order_by or 'cercania').strip().lower()
    if normalized_order not in VALID_ORDER_OPTIONS:
        raise DomainValidationError(
            'El criterio de ordenamiento no es válido.',
            errors={'orden': ['Debe ser cercania, precio o disponibilidad.']},
        )

    radius_float = None
    if radius_km is not None and radius_km != '' and lat_float is not None and lng_float is not None:
        radius_float = _parse_float_param(radius_km, 'radio_km', 0.000001, 40075.0, required=False)

    reference_dt = current_dt or timezone.now()
    active_reservations_subquery = (
        Reserva.objects.filter(cochera_id=OuterRef('pk'))
        .filter(_active_occupancy_q(reference_dt))
        .order_by()
        .values('cochera_id')
        .annotate(active_count=Count('id_reserva'))
        .values('active_count')[:1]
    )

    distance_expr = (
        _build_haversine_distance_expression(lat_float, lng_float)
        if lat_float is not None and lng_float is not None
        else Value(0.0, output_field=FloatField())
    )

    queryset = (
        Cochera.objects.filter(estado_cochera=GarageStatus.APROBADA)
        .select_related('arrendador')
        .prefetch_related('fotos', 'disponibilidades')
        .annotate(
            distancia_km=distance_expr,
            _active_reservations_now=Coalesce(
                Subquery(active_reservations_subquery, output_field=IntegerField()),
                Value(0),
            ),
        )
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

    if radius_float is not None and not has_query:
        queryset = queryset.filter(distancia_km__lte=radius_float)

    garages = list(queryset)

    for garage in garages:
        raw_dist = float(garage.distancia_km or 0.0)
        garage._raw_distance_km = raw_dist
        garage._active_reservations_ref_dt = reference_dt
        garage.distancia_km = round(raw_dist, 2)
        is_open, available_spaces = evaluate_garage_realtime_availability(garage, reference_dt)
        garage.abierta_ahora = is_open
        garage.espacios_disponibles_ahora = available_spaces
        if has_query:
            sim_score, is_rec = compute_garage_search_similarity(
                cleaned_query,
                garage.nombre,
                garage.direccion,
            )
            garage.similitud_busqueda = sim_score
            garage.es_recomendada = is_rec
        else:
            garage.similitud_busqueda = 0.0
            garage.es_recomendada = False

    if radius_float is not None and has_query:
        garages = [
            g for g in garages if g.es_recomendada or g._raw_distance_km <= radius_float
        ]

    def _secondary_sort_key(g: Cochera):
        if normalized_order == 'precio':
            return (g.tarifa_hora, g._raw_distance_km, g.id_cochera)
        if normalized_order == 'disponibilidad':
            return (-g.espacios_disponibles_ahora, g._raw_distance_km, g.id_cochera)
        return (g._raw_distance_km, g.id_cochera)

    if has_query:
        garages.sort(
            key=lambda g: (
                0 if g.es_recomendada else 1,
                -g.similitud_busqueda,
                *_secondary_sort_key(g),
            )
        )
    else:
        garages.sort(key=_secondary_sort_key)

    return garages
