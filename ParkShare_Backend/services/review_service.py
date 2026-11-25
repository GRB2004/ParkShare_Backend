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
