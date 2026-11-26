import base64
import binascii
import os
from pathlib import Path
import re
import unicodedata
import uuid

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.utils import timezone

from ParkShare_Backend.authentication import generate_tokens_for_user
from ParkShare_Backend.models import (
    AccountStatus,
    DocumentoArrendador,
    SuspensionCuenta,
    SuspensionStatus,
    UserRole,
    Usuario,
    VerificationStatus,
)

REJECTION_REASON_CATALOG = (
    'INE vencida',
    'Constancia inactiva',
    'Datos no coinciden',
    'Otro',
)

DEFAULT_MAX_ATTACHMENT_SIZE_BYTES = 20 * 1024 * 1024
FOLDER_ALLOWED_EXTENSIONS: dict[str, tuple[str, ...]] = {
    'docs': ('.pdf', '.jpg', '.jpeg', '.png'),
    'evidencias': ('.pdf', '.jpg', '.jpeg', '.png', '.webp'),
    'cocheras': ('.jpg', '.jpeg', '.png', '.webp'),
}
MIME_EXTENSION_MAP: dict[str, str] = {
    'application/pdf': '.pdf',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
}


class DomainValidationError(Exception):
    """Domain validation error mapping to HTTP 400 Bad Request."""

    def __init__(self, detail: str, errors: dict | None = None, error_code: str | None = None):
        super().__init__(detail)
        self.detail = detail
        self.errors = errors or {}
        self.error_code = error_code


class AuthenticationError(Exception):
    """Invalid credentials error mapping to HTTP 401 Unauthorized."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class AccountSuspendedError(Exception):
    """Suspended account error mapping to HTTP 403 Forbidden."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFoundError(Exception):
    """Missing resource error mapping to HTTP 404 Not Found."""

    def __init__(self, detail: str, error_code: str | None = None):
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code


class ConflictError(Exception):
    """State conflict error mapping to HTTP 409 Conflict."""

    def __init__(
        self,
        detail: str,
        error_code: str | None = None,
        extra_data: dict | None = None,
    ):
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code
        self.extra_data = extra_data or {}


class PermissionDeniedError(Exception):
    """Role or ownership permission error mapping to HTTP 403 Forbidden."""

    def __init__(self, detail: str, error_code: str | None = None):
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code


class GoneError(Exception):
    """Expired resource error mapping to HTTP 410 Gone."""

    def __init__(self, detail: str, error_code: str = 'QRExpired'):
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code


def _validation_error_to_dict(exc: DjangoValidationError) -> dict:
    if hasattr(exc, 'message_dict'):
        return exc.message_dict
    return {'non_field_errors': exc.messages}


def _build_and_validate_landlord_document(data: dict) -> DocumentoArrendador:
    doc_errors = {}
    url_ine = (data.get('url_ine') or '').strip()
    url_csf = (data.get('url_csf') or '').strip()
    url_propiedad = (data.get('url_propiedad') or '').strip()

    if not url_ine:
        doc_errors['url_ine'] = ['La identificación oficial (INE) es obligatoria.']
    if not url_csf:
        doc_errors['url_csf'] = ['La constancia de situación fiscal es obligatoria.']
    if not url_propiedad:
        doc_errors['url_propiedad'] = ['El comprobante de propiedad es obligatorio.']

    if doc_errors:
        raise DomainValidationError(
            'Faltan documentos obligatorios para el registro de arrendador.',
            errors=doc_errors,
        )

    document = DocumentoArrendador(
        url_ine=url_ine,
        url_csf=url_csf,
        url_propiedad=url_propiedad,
        estado_verificacion=VerificationStatus.PENDIENTE,
    )
    try:
        document.full_clean(exclude=['arrendador'])
    except DjangoValidationError as exc:
        raise DomainValidationError(
            'Los documentos adjuntos no cumplen con el formato requerido.',
            errors=_validation_error_to_dict(exc),
        ) from exc

    return document


def register_user(data: dict) -> dict:
    """Register a new tenant or landlord atomically."""
    role = data.get('rol', UserRole.ARRENDATARIO)
    if role not in (UserRole.ARRENDATARIO, UserRole.ARRENDADOR):
        raise DomainValidationError(
            'El rol especificado no está permitido para el registro público.',
            errors={'rol': ['Solo se permite el registro como ARRENDATARIO o ARRENDADOR.']},
        )

    raw_email = (data.get('correo') or '').strip().lower()
    if raw_email and Usuario.objects.filter(correo=raw_email).exists():
        raise DomainValidationError(
            'Ya existe una cuenta registrada con este correo electrónico.',
            errors={'correo': ['Este correo electrónico ya está registrado.']},
        )

    raw_password = data.get('password') or ''
    initial_status = (
        AccountStatus.PENDIENTE if role == UserRole.ARRENDADOR else AccountStatus.ACTIVA
    )

    user = Usuario(
        nombre_completo=(data.get('nombre_completo') or '').strip(),
        correo=raw_email,
        telefono=(data.get('telefono') or '').strip(),
        fecha_nacimiento=data.get('fecha_nacimiento'),
        rol=role,
        estado_cuenta=initial_status,
        placas=(data.get('placas') or '').strip() or None,
        modelo_vehiculo=(data.get('modelo_vehiculo') or '').strip() or None,
        cuenta_cobro=(data.get('cuenta_cobro') or '').strip() or None,
    )

    try:
        validate_password(raw_password, user=user)
    except DjangoValidationError as exc:
        raise DomainValidationError(
            'La contraseña no cumple con los requisitos mínimos de seguridad.',
            errors={'password': list(exc.messages)},
        ) from exc

    user.set_password(raw_password)

    try:
        user.full_clean()
    except DjangoValidationError as exc:
        raise DomainValidationError(
            'Los datos proporcionados contienen errores de validación.',
            errors=_validation_error_to_dict(exc),
        ) from exc

    document = _build_and_validate_landlord_document(data) if role == UserRole.ARRENDADOR else None

    with transaction.atomic():
        user.save()
        if document is not None:
            document.arrendador = user
            document.save()

    return {'usuario': user, 'documento': document}


def authenticate_user(correo: str, password: str) -> dict:
    """Authenticate by email/password, block suspended accounts, and issue JWT tokens."""
    normalized_email = (correo or '').strip().lower()
    user = Usuario.objects.filter(correo=normalized_email).first()

    if user is None or not user.check_password(password or ''):
        raise AuthenticationError('Correo o contraseña incorrectos.')

    if user.estado_cuenta == AccountStatus.SUSPENDIDA:
        active_suspension = (
            SuspensionCuenta.objects.filter(usuario=user, estado=SuspensionStatus.ACTIVA)
            .order_by('-fecha_suspension')
            .first()
        )
        reason = active_suspension.motivo if active_suspension else None
        message = (
            f'Tu cuenta ha sido suspendida. Motivo: {reason}'
            if reason
            else 'Tu cuenta ha sido suspendida.'
        )
        raise AccountSuspendedError(message)

    user.last_login = timezone.now()
    user.save(update_fields=['last_login'])

    rejection_reason = None
    if user.estado_cuenta == AccountStatus.RECHAZADA:
        latest_doc = (
            DocumentoArrendador.objects.filter(
                arrendador=user,
                estado_verificacion=VerificationStatus.RECHAZADO,
            )
            .order_by('-id_documento')
            .first()
        )
        if latest_doc:
            rejection_reason = latest_doc.motivo_rechazo

    tokens = generate_tokens_for_user(user)
    return {
        'access': tokens['access'],
        'refresh': tokens['refresh'],
        'usuario': user,
        'motivo_rechazo': rejection_reason,
    }


def review_landlord_request(
    document_id: int,
    verification_status: str,
    rejection_reason: str | None = None,
    reason_detail: str | None = None,
) -> DocumentoArrendador:
    """Resolve a pending landlord verification request (CU-10 / RN-08)."""
    if verification_status not in (VerificationStatus.APROBADO, VerificationStatus.RECHAZADO):
        raise DomainValidationError(
            'El estado de verificación indicado no es válido.',
            errors={'estado_verificacion': ['Debe ser APROBADO o RECHAZADO.']},
        )

    with transaction.atomic():
        document = (
            DocumentoArrendador.objects.select_related('arrendador')
            .select_for_update()
            .filter(id_documento=document_id)
            .first()
        )
        if document is None:
            raise NotFoundError('Solicitud de arrendador no encontrada.')

        if document.estado_verificacion != VerificationStatus.PENDIENTE:
            raise ConflictError('La solicitud ya fue resuelta anteriormente.')

        if document.arrendador.estado_cuenta == AccountStatus.SUSPENDIDA:
            raise ConflictError('No se puede resolver la solicitud de una cuenta suspendida.')

        if verification_status == VerificationStatus.APROBADO:
            document.estado_verificacion = VerificationStatus.APROBADO
            document.motivo_rechazo = None
            document.arrendador.estado_cuenta = AccountStatus.ACTIVA
        else:
            reason = (rejection_reason or '').strip()
            if reason not in REJECTION_REASON_CATALOG:
                raise DomainValidationError(
                    'El motivo de rechazo es obligatorio y debe pertenecer al catálogo permitido.',
                    errors={'motivo_rechazo': ['Motivo de rechazo inválido o faltante.']},
                )

            detail = (reason_detail or '').strip()
            if reason == 'Otro' and not detail:
                raise DomainValidationError(
                    'Debe especificar el detalle cuando el motivo es "Otro".',
                    errors={'detalle_motivo': ['El detalle del motivo es obligatorio cuando se selecciona "Otro".']},
                )

            full_reason = f'{reason}: {detail}' if detail else reason
            document.estado_verificacion = VerificationStatus.RECHAZADO
            document.motivo_rechazo = full_reason
            document.arrendador.estado_cuenta = AccountStatus.RECHAZADA

        document.arrendador.save(update_fields=['estado_cuenta', 'is_active'])
        document.save(update_fields=['estado_verificacion', 'motivo_rechazo'])

    return document


def suspend_account(target_user_id: int, admin_user: Usuario, reason: str) -> SuspensionCuenta:
    """Suspend a user account, record suspension event, and invalidate prior tokens (CU-11)."""
    cleaned_reason = (reason or '').strip()
    if not cleaned_reason:
        raise DomainValidationError(
            'El motivo de suspensión es obligatorio.',
            errors={'motivo': ['Este campo es obligatorio.']},
        )

    with transaction.atomic():
        target_user = (
            Usuario.objects.select_for_update()
            .filter(id_usuario=target_user_id)
            .first()
        )
        if target_user is None:
            raise NotFoundError('Usuario no encontrado.')

        if target_user.id_usuario == admin_user.id_usuario:
            raise DomainValidationError('Un administrador no puede suspender su propia cuenta.')

        if target_user.rol == UserRole.ADMIN:
            raise DomainValidationError('No se puede suspender una cuenta con rol de administrador.')

        if target_user.estado_cuenta == AccountStatus.SUSPENDIDA:
            raise ConflictError('La cuenta ya se encuentra suspendida.')

        suspension = SuspensionCuenta.objects.create(
            usuario=target_user,
            admin=admin_user,
            motivo=cleaned_reason,
            estado=SuspensionStatus.ACTIVA,
        )
        target_user.estado_cuenta = AccountStatus.SUSPENDIDA
        target_user.token_version += 1
        target_user.save(update_fields=['estado_cuenta', 'token_version', 'is_active'])

    return suspension


def _get_max_attachment_size_bytes() -> int:
    return int(getattr(settings, 'MAX_ATTACHMENT_SIZE_BYTES', DEFAULT_MAX_ATTACHMENT_SIZE_BYTES))


def _format_max_size_label(max_bytes: int) -> str:
    if max_bytes >= 1024 * 1024 and max_bytes % (1024 * 1024) == 0:
        return f'{max_bytes // (1024 * 1024)} MB'
    if max_bytes >= 1024 * 1024:
        return f'{max_bytes / (1024 * 1024):.1f} MB'
    if max_bytes >= 1024:
        return f'{max_bytes // 1024} KB'
    return f'{max_bytes} bytes'


def build_uploaded_file_from_base64(
    base64_content: str,
    filename: str = 'documento.pdf',
    mime_type: str | None = None,
) -> ContentFile:
    """Decode a Base64 payload from mobile clients into a Django ContentFile enforcing size limits."""
    if not isinstance(base64_content, str) or not base64_content.strip():
        raise DomainValidationError(
            'Debes adjuntar un archivo válido.',
            errors={'archivo': ['El contenido del archivo es obligatorio.']},
        )

    cleaned_b64 = base64_content.strip()
    if cleaned_b64.startswith('data:') and ',' in cleaned_b64:
        header, cleaned_b64 = cleaned_b64.split(',', 1)
        if not mime_type and ';' in header:
            mime_type = header[5:].split(';', 1)[0].strip()

    max_bytes = _get_max_attachment_size_bytes()
    max_label = _format_max_size_label(max_bytes)
    # Fast pre-check before allocating decoded buffer (~4/3 Base64 expansion + whitespace tolerance)
    if len(cleaned_b64) > int(max_bytes * 1.4) + 1024:
        raise DomainValidationError(
            f'El archivo adjunto supera el tamaño máximo permitido de {max_label}.',
            errors={'archivo': [f'El tamaño máximo permitido por archivo es de {max_label}.']},
        )

    try:
        raw_bytes = base64.b64decode(cleaned_b64, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise DomainValidationError(
            'No se pudo decodificar el archivo adjunto enviado.',
            errors={'archivo': ['Codificación Base64 inválida.']},
        ) from exc

    if not raw_bytes:
        raise DomainValidationError(
            'El archivo adjunto está vacío.',
            errors={'archivo': ['Selecciona un archivo con contenido válido.']},
        )
    if len(raw_bytes) > max_bytes:
        raise DomainValidationError(
            f'El archivo adjunto supera el tamaño máximo permitido de {max_label}.',
            errors={'archivo': [f'El tamaño máximo permitido por archivo es de {max_label}.']},
        )

    content_file = ContentFile(raw_bytes, name=(filename or 'documento').strip() or 'documento')
    if mime_type:
        content_file.content_type = mime_type.strip()
    return content_file


def save_uploaded_attachment(
    uploaded_file,
    folder: str = 'docs',
    allowed_extensions_override: tuple[str, ...] | None = None,
) -> dict:
    """Validate, sanitize, and persist an uploaded attachment under MEDIA_ROOT/<folder>/."""
    if uploaded_file is None or not hasattr(uploaded_file, 'read'):
        raise DomainValidationError(
            'Debes adjuntar un archivo válido.',
            errors={'archivo': ['El archivo adjunto es obligatorio.']},
        )

    normalized_folder = (folder or 'docs').strip().lower() if isinstance(folder, str) else ''
    if normalized_folder not in FOLDER_ALLOWED_EXTENSIONS:
        raise DomainValidationError(
            'La carpeta de destino solicitada no es válida.',
            errors={'carpeta': [f'Valores permitidos: {", ".join(FOLDER_ALLOWED_EXTENSIONS.keys())}.']},
        )

    max_bytes = _get_max_attachment_size_bytes()
    max_label = _format_max_size_label(max_bytes)
    file_size = getattr(uploaded_file, 'size', None)
    if file_size is None or file_size <= 0:
        raise DomainValidationError(
            'El archivo adjunto está vacío.',
            errors={'archivo': ['Selecciona un archivo con contenido válido.']},
        )
    if file_size > max_bytes:
        raise DomainValidationError(
            f'El archivo adjunto supera el tamaño máximo permitido de {max_label}.',
            errors={'archivo': [f'El tamaño máximo permitido por archivo es de {max_label}.']},
        )

    original_name = os.path.basename(str(getattr(uploaded_file, 'name', '') or '').strip())
    raw_base, raw_ext = os.path.splitext(original_name)
    ext = raw_ext.lower()
    if not ext:
        content_type = str(getattr(uploaded_file, 'content_type', '') or '').strip().lower()
        ext = MIME_EXTENSION_MAP.get(content_type, '')

    allowed_exts = allowed_extensions_override or FOLDER_ALLOWED_EXTENSIONS[normalized_folder]
    if ext not in allowed_exts:
        allowed_str = ', '.join(allowed_exts)
        raise DomainValidationError(
            f'Formato de archivo no permitido. Extensiones válidas: {allowed_str}.',
            errors={'archivo': [f'Extensiones permitidas: {allowed_str}.']},
        )

    ascii_base = (
        unicodedata.normalize('NFKD', raw_base or 'archivo')
        .encode('ascii', 'ignore')
        .decode('ascii')
    )
    safe_base = re.sub(r'[^A-Za-z0-9_-]+', '_', ascii_base).strip('_')[:50] or 'archivo'
    unique_filename = f'{uuid.uuid4().hex[:12]}_{safe_base}{ext}'

    media_root = Path(settings.MEDIA_ROOT).resolve()
    target_dir = (media_root / normalized_folder).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    storage = FileSystemStorage(location=str(target_dir))
    saved_name = storage.save(unique_filename, uploaded_file)

    media_prefix = (getattr(settings, 'MEDIA_URL', '/media/') or '/media/').rstrip('/')
    if not media_prefix.startswith('/'):
        media_prefix = f'/{media_prefix}'
    relative_url = f'{media_prefix}/{normalized_folder}/{saved_name}'

    return {
        'mensaje': 'Archivo subido exitosamente.',
        'url': relative_url,
        'url_archivo': relative_url,
        'nombre_archivo': saved_name,
        'carpeta': normalized_folder,
        'tamano_bytes': file_size,
    }


def resolve_media_file_path(subpath: str) -> Path:
    """Safely resolve a relative subpath inside MEDIA_ROOT preventing directory traversal."""
    cleaned = (subpath or '').strip().lstrip('/\\')
    if not cleaned or '\x00' in cleaned:
        raise NotFoundError('Archivo adjunto no encontrado.')

    media_root = Path(settings.MEDIA_ROOT).resolve()
    candidate = (media_root / cleaned).resolve()
    if not candidate.is_relative_to(media_root) or not candidate.is_file():
        raise NotFoundError('Archivo adjunto no encontrado.')

    return candidate


def delete_saved_attachment_by_url(relative_url: str | None) -> None:
    """Remove a saved file inside MEDIA_ROOT if a composite request fails validation."""
    cleaned = (relative_url or '').strip()
    media_prefix = (getattr(settings, 'MEDIA_URL', '/media/') or '/media/').rstrip('/') + '/'
    if not cleaned.startswith(media_prefix):
        return
    subpath = cleaned[len(media_prefix):]
    try:
        path = resolve_media_file_path(subpath)
        path.unlink(missing_ok=True)
    except (NotFoundError, OSError):
        return


def resubmit_landlord_documents(user: Usuario, data: dict) -> dict:
    """Allow a RECHAZADA landlord to submit corrected documents and transition back to PENDIENTE (RN-08)."""
    if user is None or not getattr(user, 'is_authenticated', False):
        raise AuthenticationError('Debes iniciar sesión para reenviar tus documentos.')

    if user.rol != UserRole.ARRENDADOR:
        raise PermissionDeniedError('Solo las cuentas de arrendador pueden reenviar documentación.')

    if user.estado_cuenta == AccountStatus.SUSPENDIDA:
        raise AccountSuspendedError('Tu cuenta se encuentra suspendida y no puede enviar solicitudes.')

    if user.estado_cuenta != AccountStatus.RECHAZADA:
        raise ConflictError(
            'Solo puedes reenviar documentos cuando tu solicitud haya sido rechazada.'
        )

    new_document = _build_and_validate_landlord_document(data)

    with transaction.atomic():
        locked_user = (
            Usuario.objects.select_for_update()
            .filter(id_usuario=user.id_usuario)
            .first()
        )
        if locked_user is None:
            raise NotFoundError('Usuario no encontrado.')

        if locked_user.estado_cuenta == AccountStatus.SUSPENDIDA:
            raise AccountSuspendedError(
                'Tu cuenta se encuentra suspendida y no puede enviar solicitudes.'
            )

        if locked_user.estado_cuenta != AccountStatus.RECHAZADA:
            raise ConflictError(
                'Solo puedes reenviar documentos cuando tu solicitud haya sido rechazada.'
            )

        new_document.arrendador = locked_user
        new_document.estado_verificacion = VerificationStatus.PENDIENTE
        new_document.motivo_rechazo = None
        new_document.save()

        locked_user.estado_cuenta = AccountStatus.PENDIENTE
        locked_user.save(update_fields=['estado_cuenta', 'is_active'])

    return {'usuario': locked_user, 'documento': new_document}
