import mimetypes

from django.http import FileResponse
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ParkShare_Backend.models import AccountStatus, UserRole
from ParkShare_Backend.serializers.auth_serializers import (
    LandlordDocumentSummarySerializer,
    LoginSerializer,
    RegisterSerializer,
    serialize_registered_user,
)
from ParkShare_Backend.services.auth_service import (
    AccountSuspendedError,
    AuthenticationError,
    ConflictError,
    DomainValidationError,
    GoneError,
    NotFoundError,
    PermissionDeniedError,
    authenticate_user,
    delete_saved_attachment_by_url,
    register_user,
    resolve_media_file_path,
    save_uploaded_attachment,
)

EXPLICIT_CONTENT_TYPES: dict[str, str] = {
    '.pdf': 'application/pdf',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
}


class StandardPagination(PageNumberPagination):
    """Standard DRF pagination supporting page_size query parameter."""

    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


def handle_domain_exception(exc: Exception) -> Response:
    """Map domain exceptions to uniform JSON error responses in Spanish."""
    if isinstance(exc, DomainValidationError):
        payload = {'detail': exc.detail}
        if exc.errors:
            payload['errors'] = exc.errors
        if getattr(exc, 'error_code', None):
            payload['error'] = exc.error_code
        return Response(payload, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, AuthenticationError):
        return Response({'detail': exc.detail}, status=status.HTTP_401_UNAUTHORIZED)
    if isinstance(exc, AccountSuspendedError):
        return Response(
            {
                'detail': exc.detail,
                'codigo': 'CUENTA_SUSPENDIDA',
                'estado_cuenta': AccountStatus.SUSPENDIDA,
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    if isinstance(exc, PermissionDeniedError):
        payload = {'detail': exc.detail}
        if getattr(exc, 'error_code', None):
            payload['error'] = exc.error_code
        return Response(payload, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, NotFoundError):
        payload = {'detail': exc.detail}
        if getattr(exc, 'error_code', None):
            payload['error'] = exc.error_code
        return Response(payload, status=status.HTTP_404_NOT_FOUND)
    if isinstance(exc, ConflictError):
        payload = {'detail': exc.detail}
        if getattr(exc, 'error_code', None):
            payload['error'] = exc.error_code
        if getattr(exc, 'extra_data', None):
            payload.update(exc.extra_data)
        return Response(payload, status=status.HTTP_409_CONFLICT)
    if isinstance(exc, GoneError):
        return Response({'error': exc.error_code, 'detail': exc.detail}, status=status.HTTP_410_GONE)
    raise exc


def validation_error_response(errors: dict) -> Response:
    return Response(
        {
            'detail': 'Los datos proporcionados contienen errores de validación.',
            'errors': errors,
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


class RegisterView(APIView):
    """Public user registration endpoint (`POST /auth/registro`)."""

    permission_classes = [AllowAny]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request):
        file_field_mappings = (
            ('url_ine', ('archivo_ine', 'ine', 'url_ine'), ('.pdf', '.jpg', '.jpeg', '.png')),
            ('url_csf', ('archivo_csf', 'csf', 'url_csf'), ('.pdf',)),
            ('url_propiedad', ('archivo_propiedad', 'propiedad', 'url_propiedad'), ('.pdf',)),
        )
        excluded_file_keys = {
            alias for _, aliases, _ in file_field_mappings for alias in aliases if alias not in ('url_ine', 'url_csf', 'url_propiedad')
        }
        payload = {
            key: val
            for key, val in request.data.items()
            if key not in excluded_file_keys and not hasattr(val, 'read')
        }

        saved_urls: list[str] = []
        try:
            for target_key, aliases, allowed_exts in file_field_mappings:
                uploaded = None
                for alias in aliases:
                    if alias in request.FILES:
                        uploaded = request.FILES[alias]
                        break
                if uploaded is not None:
                    saved_info = save_uploaded_attachment(
                        uploaded,
                        folder='docs',
                        allowed_extensions_override=allowed_exts,
                    )
                    saved_urls.append(saved_info['url'])
                    payload[target_key] = saved_info['url']

            serializer = RegisterSerializer(data=payload)
            if not serializer.is_valid():
                for url in saved_urls:
                    delete_saved_attachment_by_url(url)
                return validation_error_response(serializer.errors)

            result = register_user(serializer.validated_data)
        except (DomainValidationError, ConflictError) as exc:
            for url in saved_urls:
                delete_saved_attachment_by_url(url)
            return handle_domain_exception(exc)
        except Exception:
            for url in saved_urls:
                delete_saved_attachment_by_url(url)
            raise

        user = result['usuario']
        document = result['documento']

        if user.rol == UserRole.ARRENDADOR:
            return Response(
                {
                    'mensaje': 'Solicitud de arrendador registrada. Tu cuenta quedará activa tras la verificación del administrador.',
                    'usuario': serialize_registered_user(user),
                    'documento': LandlordDocumentSummarySerializer(document).data,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {
                'mensaje': 'Cuenta de arrendatario creada exitosamente.',
                'usuario': serialize_registered_user(user),
            },
            status=status.HTTP_201_CREATED,
        )


class AttachmentUploadView(APIView):
    """Upload physical attachment files (`POST /archivos/subir`) for landlord registration or appeals."""

    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded_file = request.FILES.get('archivo') or request.FILES.get('file')
        folder = request.data.get('carpeta', 'docs')
        try:
            info = save_uploaded_attachment(uploaded_file=uploaded_file, folder=folder)
        except DomainValidationError as exc:
            return handle_domain_exception(exc)

        info['archivo_url'] = request.build_absolute_uri(info['url'])
        return Response(info, status=status.HTTP_201_CREATED)


class MediaAttachmentServeView(APIView):
    """Serve stored attachment files under `/media/<subpath>` safely."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, subpath: str):
        try:
            file_path = resolve_media_file_path(subpath)
        except NotFoundError as exc:
            return handle_domain_exception(exc)

        ext = file_path.suffix.lower()
        content_type = EXPLICIT_CONTENT_TYPES.get(ext)
        if not content_type:
            guessed, _ = mimetypes.guess_type(str(file_path))
            content_type = guessed or 'application/octet-stream'

        return FileResponse(
            open(file_path, 'rb'),
            content_type=content_type,
            as_attachment=False,
            filename=file_path.name,
        )


class LoginView(APIView):
    """Public authentication endpoint (`POST /auth/login`)."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            result = authenticate_user(
                correo=serializer.validated_data['correo'],
                password=serializer.validated_data['password'],
            )
        except (AuthenticationError, AccountSuspendedError) as exc:
            return handle_domain_exception(exc)

        user = result['usuario']
        return Response(
            {
                'access': result['access'],
                'refresh': result['refresh'],
                'usuario': {
                    'id_usuario': user.id_usuario,
                    'nombre_completo': user.nombre_completo,
                    'correo': user.correo,
                    'telefono': user.telefono,
                    'rol': user.rol,
                    'estado_cuenta': user.estado_cuenta,
                    'motivo_rechazo': result['motivo_rechazo'],
                },
            },
            status=status.HTTP_200_OK,
        )
