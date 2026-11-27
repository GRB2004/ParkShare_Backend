from datetime import datetime

from django.db import transaction
from django.utils import timezone

from ParkShare_Backend.models import (
    AccountStatus,
    Resena,
    Reserva,
    ReservationStatus,
    UserRole,
    Usuario,
)
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)


def _check_active_participant_user(user: Usuario) -> None:
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.estado_cuenta != AccountStatus.ACTIVA
        or user.rol not in (UserRole.ARRENDATARIO, UserRole.ARRENDADOR)
    ):
        raise PermissionDeniedError(
            'No tienes permiso para emitir una reseña sobre esta reservación.'
        )


def validate_review_payload(
    calificacion,
    etiquetas: str | None = None,
    comentario: str | None = None,
) -> tuple[int, str | None, str | None]:
    if isinstance(calificacion, bool) or not isinstance(calificacion, int):
        raise DomainValidationError(
            'La calificación debe ser un número entero entre 1 y 5.',
            errors={'calificacion': ['La calificación debe ser un número entero entre 1 y 5.']},
        )
    if calificacion < 1 or calificacion > 5:
        raise DomainValidationError(
            'La calificación debe estar entre 1 y 5 estrellas.',
            errors={'calificacion': ['La calificación debe estar entre 1 y 5.']},
        )

    cleaned_tags = None
    if etiquetas is not None:
        if not isinstance(etiquetas, str):
            raise DomainValidationError(
                'El formato de las etiquetas no es válido.',
                errors={'etiquetas': ['Las etiquetas deben enviarse como cadena de texto.']},
            )
        if len(etiquetas) > 255:
            raise DomainValidationError(
                'Las etiquetas no pueden exceder 255 caracteres.',
                errors={'etiquetas': ['Máximo 255 caracteres permitidos.']},
            )
        stripped_tags = etiquetas.strip()
        cleaned_tags = stripped_tags if stripped_tags else None

    cleaned_comment = None
    if comentario is not None:
        if not isinstance(comentario, str):
            raise DomainValidationError(
                'El comentario debe ser una cadena de texto.',
                errors={'comentario': ['Formato de comentario inválido.']},
            )
        stripped_comment = comentario.strip()
        cleaned_comment = stripped_comment if stripped_comment else None

    return calificacion, cleaned_tags, cleaned_comment


def _check_active_landlord_for_reviews(user: Usuario) -> None:
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.rol != UserRole.ARRENDADOR
        or user.estado_cuenta != AccountStatus.ACTIVA
    ):
        raise PermissionDeniedError(
            'Solo un arrendador con cuenta activa puede consultar sus calificaciones recibidas.'
        )


def list_landlord_received_reviews(
    host_user: Usuario,
    calificacion_filter: str | None = None,
) -> dict:
    """Return average rating, star distribution, and tenant reviews received by the landlord."""
    _check_active_landlord_for_reviews(host_user)

    parsed_star_filter: int | None = None
    if calificacion_filter is not None and str(calificacion_filter).strip() != '':
        raw_val = str(calificacion_filter).strip()
        if not raw_val.isdigit():
            raise DomainValidationError(
                'El filtro de calificación debe ser un número entero entre 1 y 5.',
                errors={'calificacion': ['Ingresa un valor entero entre 1 y 5.']},
            )
        parsed_star_filter = int(raw_val)
        if parsed_star_filter < 1 or parsed_star_filter > 5:
            raise DomainValidationError(
                'El filtro de calificación debe estar entre 1 y 5 estrellas.',
                errors={'calificacion': ['El filtro debe estar entre 1 y 5.']},
            )

    all_reviews = list(
        Resena.objects.select_related('autor', 'reserva', 'reserva__cochera')
        .filter(destinatario=host_user)
        .exclude(autor=host_user)
        .order_by('-fecha', '-id_resena')
    )

    total_resenas = len(all_reviews)
    distribucion = {'5': 0, '4': 0, '3': 0, '2': 0, '1': 0}
    score_sum = 0
    for rev in all_reviews:
        key = str(rev.calificacion)
        if key in distribucion:
            distribucion[key] += 1
        score_sum += int(rev.calificacion)

    promedio_calificacion = (
        round(score_sum / total_resenas, 1) if total_resenas > 0 else 0.0
    )

    filtered_reviews = (
        [rev for rev in all_reviews if rev.calificacion == parsed_star_filter]
        if parsed_star_filter is not None
        else all_reviews
    )

    items = []
    for rev in filtered_reviews:
        reserva = rev.reserva
        cochera = reserva.cochera if reserva is not None else None
        autor = rev.autor
        items.append(
            {
                'id_resena': rev.id_resena,
                'reserva_id': rev.reserva_id,
                'calificacion': rev.calificacion,
                'etiquetas': rev.etiquetas,
                'comentario': rev.comentario,
                'fecha': rev.fecha,
                'cochera_id': reserva.cochera_id if reserva is not None else None,
                'cochera_nombre': cochera.nombre if cochera is not None else 'Cochera',
                'autor': {
                    'id_usuario': rev.autor_id,
                    'nombre_completo': (
                        autor.nombre_completo if autor is not None else 'Conductor'
                    ),
                    'modelo_vehiculo': (
                        autor.modelo_vehiculo if autor is not None else None
                    ),
                },
            }
        )

    return {
        'promedio_calificacion': promedio_calificacion,
        'total_resenas': total_resenas,
        'distribucion': distribucion,
        'resenas': items,
    }


def create_review(
    reservation_id: int,
    author: Usuario,
    calificacion: int,
    etiquetas: str | None = None,
    comentario: str | None = None,
    now: datetime | None = None,
) -> Resena:
    """Create a bilateral review on a FINALIZADA reservation deriving author/recipient automatically (CU-09)."""
    _check_active_participant_user(author)
    score, cleaned_tags, cleaned_comment = validate_review_payload(
        calificacion=calificacion,
        etiquetas=etiquetas,
        comentario=comentario,
    )
    reference_now = now if now is not None else timezone.now()

    with transaction.atomic():
        reservation = (
            Reserva.objects.select_related('cochera', 'arrendatario')
            .select_for_update()
            .filter(id_reserva=reservation_id)
            .first()
        )
        if reservation is None:
            raise NotFoundError('La reservación solicitada no existe.')

        is_tenant = author.id_usuario == reservation.arrendatario_id
        is_landlord = author.id_usuario == reservation.cochera.arrendador_id
        if not (is_tenant or is_landlord):
            raise PermissionDeniedError(
                'No tienes permiso para calificar una reservación en la que no participaste.'
            )

        recipient_id = (
            reservation.cochera.arrendador_id
            if is_tenant
            else reservation.arrendatario_id
        )
        if author.id_usuario == recipient_id:
            raise DomainValidationError(
                'Un usuario no puede evaluarse a sí mismo.',
                errors={'destinatario': ['El autor y el destinatario no pueden coincidir.']},
            )

        if reservation.estado != ReservationStatus.FINALIZADA:
            raise ConflictError(
                f'Solo es posible calificar reservaciones finalizadas (estado actual: {reservation.estado}).'
            )

        if Resena.objects.filter(reserva=reservation, autor=author).exists():
            raise ConflictError('Ya has emitido una reseña para esta reservación.')

        return Resena.objects.create(
            reserva=reservation,
            autor=author,
            destinatario_id=recipient_id,
            calificacion=score,
            etiquetas=cleaned_tags,
            comentario=cleaned_comment,
            fecha=reference_now,
        )
