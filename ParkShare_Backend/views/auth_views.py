from rest_framework import status
from rest_framework.pagination import PageNumberPagination
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
    NotFoundError,
    PermissionDeniedError,
    authenticate_user,
    register_user,
)


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
        return Response({'detail': exc.detail}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, NotFoundError):
        return Response({'detail': exc.detail}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(exc, ConflictError):
        return Response({'detail': exc.detail}, status=status.HTTP_409_CONFLICT)
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

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            result = register_user(serializer.validated_data)
        except (DomainValidationError, ConflictError) as exc:
            return handle_domain_exception(exc)

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
