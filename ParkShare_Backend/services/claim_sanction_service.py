from datetime import datetime
import re

from django.db import transaction
from django.utils import timezone

from ParkShare_Backend.models import (
    AccountStatus,
    Apelacion,
    AppealStatus,
    ComplaintReasonCategory,
    ComplaintStatus,
    Queja,
    Reserva,
    ReservationStatus,
    Sancion,
    SanctionStatus,
    UserRole,
    Usuario,
)
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)

ALLOWED_COMPLAINT_RESERVATION_STATES = (
    ReservationStatus.CONFIRMADA,
    ReservationStatus.EN_USO,
    ReservationStatus.FINALIZADA,
)
ALLOWED_COMPLAINT_RESOLUTION_STATES = (
    ComplaintStatus.DESCARTADA,
    ComplaintStatus.REVISADA,
)
ALLOWED_APPEAL_RESOLUTION_STATES = (
    AppealStatus.ACEPTADA,
    AppealStatus.RECHAZADA,
)
EVIDENCE_URL_REGEX = re.compile(
    r'^(https?://[^\s/$.?#].[^\s]*|/[^\s]+|[A-Za-z0-9_./-]+\.[A-Za-z0-9]{2,5})$'
)


def _check_active_tenant(user: Usuario) -> None:
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.rol != UserRole.ARRENDATARIO
        or user.estado_cuenta != AccountStatus.ACTIVA
    ):
        raise PermissionDeniedError('Solo un arrendatario con cuenta activa puede presentar una queja.')


def _check_active_admin(user: Usuario) -> None:
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.rol != UserRole.ADMIN
        or user.estado_cuenta != AccountStatus.ACTIVA
    ):
        raise PermissionDeniedError('Se requieren permisos de administrador para realizar esta acción.')


def _check_landlord_role(user: Usuario) -> None:
    if (
        not user
        or not getattr(user, 'is_authenticated', False)
        or user.rol != UserRole.ARRENDADOR
        or user.estado_cuenta == AccountStatus.SUSPENDIDA
    ):
        raise PermissionDeniedError('Solo un arrendador autenticado puede consultar o apelar sus sanciones.')


def create_complaint(
    reservation_id: int,
    renter: Usuario,
    motivo_categoria: str,
    descripcion: str,
    now: datetime | None = None,
) -> Queja:
    """Create a tenant complaint on a CONFIRMADA, EN_USO, or FINALIZADA reservation (CU-12)."""
    _check_active_tenant(renter)

    if not isinstance(motivo_categoria, str) or motivo_categoria.strip().upper() not in ComplaintReasonCategory.values:
        raise DomainValidationError(
            'La categoría del motivo de la queja no es válida.',
            errors={'motivo_categoria': [f'Valores permitidos: {", ".join(ComplaintReasonCategory.values)}.']},
        )
    normalized_category = motivo_categoria.strip().upper()

    if not isinstance(descripcion, str) or len(descripcion.strip()) < 10:
        raise DomainValidationError(
            'La descripción de la queja debe contener al menos 10 caracteres.',
            errors={'descripcion': ['Describe lo ocurrido con al menos 10 caracteres.']},
        )
    cleaned_description = descripcion.strip()
    reference_now = now if now is not None else timezone.now()

    with transaction.atomic():
        reservation = (
            Reserva.objects.select_related('cochera', 'arrendatario')
            .select_for_update()
            .filter(id_reserva=reservation_id)
            .first()
        )
        if reservation is None:
            raise NotFoundError('La reservación indicada no existe.')

        if reservation.arrendatario_id != renter.id_usuario:
            raise PermissionDeniedError('No puedes presentar una queja sobre una reservación que no te pertenece.')

        if reservation.estado not in ALLOWED_COMPLAINT_RESERVATION_STATES:
            raise ConflictError(
                f'No es posible presentar una queja para una reservación en estado {reservation.estado}.'
            )

        return Queja.objects.create(
            reserva=reservation,
            arrendatario=renter,
            arrendador_id=reservation.cochera.arrendador_id,
            motivo_categoria=normalized_category,
            descripcion=cleaned_description,
            estado=ComplaintStatus.PENDIENTE,
            fecha_creacion=reference_now,
        )


def list_admin_claims_and_appeals(
    admin_user: Usuario,
    estado_queja: str | None = None,
    estado_apelacion: str | None = None,
) -> dict:
    """List pending complaints and appeals under review for the admin panel (GET /admin/quejas)."""
    _check_active_admin(admin_user)

    q_filter = (estado_queja or ComplaintStatus.PENDIENTE).strip().upper()
    if q_filter != 'TODAS' and q_filter not in ComplaintStatus.values:
        raise DomainValidationError(
            'El filtro estado_queja no es válido.',
            errors={'estado_queja': ['Valores permitidos: PENDIENTE, REVISADA, DESCARTADA, TODAS.']},
        )

    a_filter = (estado_apelacion or AppealStatus.EN_REVISION).strip().upper()
    if a_filter != 'TODAS' and a_filter not in AppealStatus.values:
        raise DomainValidationError(
            'El filtro estado_apelacion no es válido.',
            errors={'estado_apelacion': ['Valores permitidos: EN_REVISION, ACEPTADA, RECHAZADA, TODAS.']},
        )

    complaints_qs = Queja.objects.select_related('arrendatario', 'arrendador').order_by('-fecha_creacion', '-id_queja')
    if q_filter != 'TODAS':
        complaints_qs = complaints_qs.filter(estado=q_filter)

    appeals_qs = Apelacion.objects.select_related('sancion__arrendador').order_by('-fecha_creacion', '-id_apelacion')
    if a_filter != 'TODAS':
        appeals_qs = appeals_qs.filter(estado=a_filter)

    quejas_list = [
        {
            'id_queja': q.id_queja,
            'reserva_id': q.reserva_id,
            'motivo_categoria': q.motivo_categoria,
            'estado': q.estado,
            'arrendatario': {
                'id_usuario': q.arrendatario.id_usuario,
                'nombre_completo': q.arrendatario.nombre_completo,
            },
            'arrendador': {
                'id_usuario': q.arrendador.id_usuario,
                'nombre_completo': q.arrendador.nombre_completo,
            },
            'fecha_creacion': q.fecha_creacion,
        }
        for q in complaints_qs
    ]

    apelaciones_list = [
        {
            'id_apelacion': a.id_apelacion,
            'sancion_id': a.sancion_id,
            'arrendador': {
                'id_usuario': a.sancion.arrendador.id_usuario,
                'nombre_completo': a.sancion.arrendador.nombre_completo,
            },
            'motivo_sancion': a.sancion.motivo,
            'descripcion': a.descripcion,
            'url_evidencia': a.url_evidencia,
            'estado': a.estado,
            'fecha_creacion': a.fecha_creacion,
        }
        for a in appeals_qs
    ]

    return {
        'quejas_pendientes': quejas_list,
        'apelaciones_en_revision': apelaciones_list,
        'totales': {
            'quejas': len(quejas_list),
            'apelaciones': len(apelaciones_list),
        },
    }


def get_admin_complaint_detail(complaint_id: int, admin_user: Usuario) -> dict:
    """Return full complaint detail including active sanction count of the landlord (GET /admin/quejas/{id})."""
    _check_active_admin(admin_user)

    complaint = (
        Queja.objects.select_related(
            'reserva__cochera',
            'arrendatario',
            'arrendador',
            'sancion',
        )
        .filter(id_queja=complaint_id)
        .first()
    )
    if complaint is None:
        raise NotFoundError('Queja no encontrada.')

    active_sanctions_count = Sancion.objects.filter(
        arrendador_id=complaint.arrendador_id,
        estado=SanctionStatus.VIGENTE,
    ).count()

    sanction_obj = getattr(complaint, 'sancion', None)
    sanction_payload = None
    if sanction_obj is not None:
        sanction_payload = {
            'id_sancion': sanction_obj.id_sancion,
            'queja_id': sanction_obj.queja_id,
            'arrendador_id': sanction_obj.arrendador_id,
            'admin_id': sanction_obj.admin_id,
            'motivo': sanction_obj.motivo,
            'estado': sanction_obj.estado,
            'fecha_creacion': sanction_obj.fecha_creacion,
        }

    return {
        'id_queja': complaint.id_queja,
        'motivo_categoria': complaint.motivo_categoria,
        'descripcion': complaint.descripcion,
        'estado': complaint.estado,
        'fecha_creacion': complaint.fecha_creacion,
        'reserva': {
            'id_reserva': complaint.reserva.id_reserva,
            'estado': complaint.reserva.estado,
            'fecha_hora_inicio': complaint.reserva.fecha_hora_inicio,
            'fecha_hora_fin': complaint.reserva.fecha_hora_fin,
            'cochera': complaint.reserva.cochera.nombre,
        },
        'arrendatario': {
            'id_usuario': complaint.arrendatario.id_usuario,
            'nombre_completo': complaint.arrendatario.nombre_completo,
            'correo': complaint.arrendatario.correo,
        },
        'arrendador': {
            'id_usuario': complaint.arrendador.id_usuario,
            'nombre_completo': complaint.arrendador.nombre_completo,
            'correo': complaint.arrendador.correo,
            'sanciones_vigentes': active_sanctions_count,
        },
        'sancion': sanction_payload,
    }


def resolve_complaint(
    complaint_id: int,
    admin_user: Usuario,
    new_status: str,
    motivo_sancion: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Resolve a PENDIENTE complaint as DESCARTADA or REVISADA (creating a VIGENTE Sancion) (CU-13)."""
    _check_active_admin(admin_user)

    if not isinstance(new_status, str) or new_status.strip().upper() not in ALLOWED_COMPLAINT_RESOLUTION_STATES:
        raise DomainValidationError(
            'El estado de resolución de la queja debe ser DESCARTADA o REVISADA.',
            errors={'estado': ['Valores permitidos: DESCARTADA, REVISADA.']},
        )
    normalized_status = new_status.strip().upper()

    cleaned_reason = (motivo_sancion or '').strip() if isinstance(motivo_sancion, str) else ''
    if normalized_status == ComplaintStatus.REVISADA and len(cleaned_reason) < 5:
        raise DomainValidationError(
            'El motivo de la sanción es obligatorio al sancionar al arrendador (mínimo 5 caracteres).',
            errors={'motivo_sancion': ['Debes especificar el motivo de la sanción (mínimo 5 caracteres).']},
        )

    reference_now = now if now is not None else timezone.now()

    with transaction.atomic():
        complaint = (
            Queja.objects.select_related('arrendador')
            .select_for_update()
            .filter(id_queja=complaint_id)
            .first()
        )
        if complaint is None:
            raise NotFoundError('Queja no encontrada.')

        if complaint.estado != ComplaintStatus.PENDIENTE:
            raise ConflictError(
                f'La queja ya fue resuelta previamente (estado actual: {complaint.estado}).'
            )

        complaint.estado = normalized_status
        complaint.save(update_fields=['estado'])

        if normalized_status == ComplaintStatus.DESCARTADA:
            return {
                'id_queja': complaint.id_queja,
                'estado': complaint.estado,
                'sancion': None,
                'mensaje': 'La queja ha sido descartada.',
            }

        sanction = Sancion.objects.create(
            queja=complaint,
            arrendador_id=complaint.arrendador_id,
            admin=admin_user,
            motivo=cleaned_reason,
            estado=SanctionStatus.VIGENTE,
            fecha_creacion=reference_now,
        )
        return {
            'id_queja': complaint.id_queja,
            'estado': complaint.estado,
            'sancion': {
                'id_sancion': sanction.id_sancion,
                'queja_id': sanction.queja_id,
                'arrendador_id': sanction.arrendador_id,
                'admin_id': sanction.admin_id,
                'motivo': sanction.motivo,
                'estado': sanction.estado,
                'fecha_creacion': sanction.fecha_creacion,
            },
            'mensaje': 'Queja revisada y sanción aplicada al arrendador.',
        }


def list_landlord_sanctions(
    host_user: Usuario,
    estado_filter: str | None = None,
) -> dict:
    """List all sanctions (VIGENTE and RETIRADA) for the authenticated landlord (GET /arrendador/sanciones)."""
    _check_landlord_role(host_user)

    normalized_filter = None
    if estado_filter is not None and str(estado_filter).strip() != '':
        normalized_filter = str(estado_filter).strip().upper()
        if normalized_filter not in SanctionStatus.values:
            raise DomainValidationError(
                'El filtro de estado de sanción no es válido.',
                errors={'estado': ['Valores permitidos: VIGENTE, RETIRADA.']},
            )

    all_sanctions = list(
        Sancion.objects.select_related('apelacion')
        .filter(arrendador_id=host_user.id_usuario)
        .order_by('-fecha_creacion', '-id_sancion')
    )
    active_count = sum(1 for s in all_sanctions if s.estado == SanctionStatus.VIGENTE)

    items = []
    for sanction in all_sanctions:
        if normalized_filter is not None and sanction.estado != normalized_filter:
            continue
        appeal_obj = getattr(sanction, 'apelacion', None)
        can_appeal = sanction.estado == SanctionStatus.VIGENTE and appeal_obj is None
        items.append({
            'id_sancion': sanction.id_sancion,
            'queja_id': sanction.queja_id,
            'motivo': sanction.motivo,
            'estado': sanction.estado,
            'fecha_creacion': sanction.fecha_creacion,
            'puede_apelar': can_appeal,
            'apelacion_estado': appeal_obj.estado if appeal_obj is not None else None,
        })

    return {
        'total': len(items),
        'vigentes': active_count,
        'sanciones': items,
    }


def get_landlord_sanction_detail(sanction_id: int, host_user: Usuario) -> dict:
    """Return full sanction detail for the owning landlord (GET /arrendador/sanciones/{id})."""
    _check_landlord_role(host_user)

    sanction = (
        Sancion.objects.select_related('queja', 'apelacion')
        .filter(id_sancion=sanction_id)
        .first()
    )
    if sanction is None:
        raise NotFoundError('Sanción no encontrada.')

    if sanction.arrendador_id != host_user.id_usuario:
        raise PermissionDeniedError('No tienes permiso para consultar una sanción que no te pertenece.')

    appeal_obj = getattr(sanction, 'apelacion', None)
    appeal_payload = None
    if appeal_obj is not None:
        appeal_payload = {
            'id_apelacion': appeal_obj.id_apelacion,
            'sancion_id': appeal_obj.sancion_id,
            'descripcion': appeal_obj.descripcion,
            'url_evidencia': appeal_obj.url_evidencia,
            'estado': appeal_obj.estado,
            'fecha_creacion': appeal_obj.fecha_creacion,
        }

    return {
        'id_sancion': sanction.id_sancion,
        'queja_id': sanction.queja_id,
        'motivo': sanction.motivo,
        'estado': sanction.estado,
        'fecha_creacion': sanction.fecha_creacion,
        'queja_resumen': {
            'reserva_id': sanction.queja.reserva_id,
            'motivo_categoria': sanction.queja.motivo_categoria,
            'fecha_creacion': sanction.queja.fecha_creacion,
        },
        'apelacion': appeal_payload,
    }


def submit_appeal(
    sanction_id: int,
    host_user: Usuario,
    descripcion: str,
    url_evidencia: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Submit an appeal on a VIGENTE sanction owned by the landlord (POST /sanciones/{id}/apelar)."""
    _check_landlord_role(host_user)

    if not isinstance(descripcion, str) or len(descripcion.strip()) < 10:
        raise DomainValidationError(
            'La descripción de la apelación debe contener al menos 10 caracteres.',
            errors={'descripcion': ['Explica el motivo de tu apelación con al menos 10 caracteres.']},
        )
    cleaned_description = descripcion.strip()

    cleaned_url = None
    if url_evidencia is not None and str(url_evidencia).strip() != '':
        if not isinstance(url_evidencia, str) or len(url_evidencia.strip()) > 255:
            raise DomainValidationError(
                'La URL de evidencia no puede exceder 255 caracteres.',
                errors={'url_evidencia': ['Máximo 255 caracteres permitidos.']},
            )
        candidate_url = url_evidencia.strip()
        if not EVIDENCE_URL_REGEX.match(candidate_url):
            raise DomainValidationError(
                'El formato de url_evidencia no es válido.',
                errors={'url_evidencia': ['Debe ser una URL http(s):// o ruta de archivo válida.']},
            )
        cleaned_url = candidate_url

    reference_now = now if now is not None else timezone.now()

    with transaction.atomic():
        sanction = (
            Sancion.objects.select_for_update()
            .filter(id_sancion=sanction_id)
            .first()
        )
        if sanction is None:
            raise NotFoundError('Sanción no encontrada.')

        if sanction.arrendador_id != host_user.id_usuario:
            raise PermissionDeniedError('No puedes apelar una sanción que no pertenece a tu cuenta.')

        if sanction.estado != SanctionStatus.VIGENTE:
            raise ConflictError(
                f'Solo se pueden apelar sanciones vigentes (estado actual: {sanction.estado}).'
            )

        if Apelacion.objects.filter(sancion=sanction).exists():
            raise ConflictError('Esta sanción ya cuenta con una apelación registrada.')

        appeal = Apelacion.objects.create(
            sancion=sanction,
            descripcion=cleaned_description,
            url_evidencia=cleaned_url,
            estado=AppealStatus.EN_REVISION,
            fecha_creacion=reference_now,
        )

        return {
            'id_apelacion': appeal.id_apelacion,
            'sancion_id': appeal.sancion_id,
            'descripcion': appeal.descripcion,
            'url_evidencia': appeal.url_evidencia,
            'estado': appeal.estado,
            'fecha_creacion': appeal.fecha_creacion,
            'mensaje': 'Tu apelación fue enviada y está en revisión por el administrador.',
        }


def resolve_appeal(
    appeal_id: int,
    admin_user: Usuario,
    new_status: str,
) -> dict:
    """Resolve an EN_REVISION appeal as ACEPTADA (retiring sanction) or RECHAZADA (keeping sanction) (CU-13)."""
    _check_active_admin(admin_user)

    if not isinstance(new_status, str) or new_status.strip().upper() not in ALLOWED_APPEAL_RESOLUTION_STATES:
        raise DomainValidationError(
            'El estado de resolución de la apelación debe ser ACEPTADA o RECHAZADA.',
            errors={'estado': ['Valores permitidos: ACEPTADA, RECHAZADA.']},
        )
    normalized_status = new_status.strip().upper()

    with transaction.atomic():
        appeal = (
            Apelacion.objects.select_related('sancion')
            .select_for_update()
            .filter(id_apelacion=appeal_id)
            .first()
        )
        if appeal is None:
            raise NotFoundError('Apelación no encontrada.')

        if appeal.estado != AppealStatus.EN_REVISION:
            raise ConflictError(
                f'La apelación ya fue resuelta anteriormente (estado actual: {appeal.estado}).'
            )

        sanction = Sancion.objects.select_for_update().get(pk=appeal.sancion_id)
        appeal.estado = normalized_status
        appeal.save(update_fields=['estado'])

        if normalized_status == AppealStatus.ACEPTADA:
            sanction.estado = SanctionStatus.RETIRADA
            sanction.save(update_fields=['estado'])
            message = 'Apelación aceptada. La sanción ha sido retirada.'
        else:
            message = 'Apelación rechazada. La sanción permanece vigente.'

        return {
            'id_apelacion': appeal.id_apelacion,
            'sancion_id': sanction.id_sancion,
            'estado': appeal.estado,
            'sancion_estado': sanction.estado,
            'mensaje': message,
        }
