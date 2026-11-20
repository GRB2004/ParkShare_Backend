from datetime import timedelta
import uuid

from django.conf import settings
from django.utils import timezone
import jwt
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .models import AccountStatus, Usuario


def _get_jwt_config() -> dict:
    return getattr(settings, 'JWT_SETTINGS', {})


def generate_tokens_for_user(user: Usuario) -> dict[str, str]:
    """Issue access and refresh JWT tokens embedding the current token_version."""
    config = _get_jwt_config()
    algorithm = config.get('ALGORITHM', 'HS256')
    access_minutes = config.get('ACCESS_TOKEN_LIFETIME_MINUTES', 60)
    refresh_days = config.get('REFRESH_TOKEN_LIFETIME_DAYS', 7)
    now = timezone.now()

    base_claims = {
        'user_id': user.id_usuario,
        'correo': user.correo,
        'rol': user.rol,
        'token_version': user.token_version,
        'iat': int(now.timestamp()),
    }

    access_payload = {
        **base_claims,
        'token_type': 'access',
        'jti': str(uuid.uuid4()),
        'exp': int((now + timedelta(minutes=access_minutes)).timestamp()),
    }
    refresh_payload = {
        **base_claims,
        'token_type': 'refresh',
        'jti': str(uuid.uuid4()),
        'exp': int((now + timedelta(days=refresh_days)).timestamp()),
    }

    return {
        'access': jwt.encode(access_payload, settings.SECRET_KEY, algorithm=algorithm),
        'refresh': jwt.encode(refresh_payload, settings.SECRET_KEY, algorithm=algorithm),
    }


def decode_access_token(token: str) -> dict:
    """Decode and validate an access JWT token."""
    algorithm = _get_jwt_config().get('ALGORITHM', 'HS256')
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[algorithm])
    except jwt.PyJWTError as exc:
        raise AuthenticationFailed('Token inválido o expirado.') from exc

    if payload.get('token_type') != 'access':
        raise AuthenticationFailed('Tipo de token inválido para esta operación.')
    return payload


class CustomJWTAuthentication(BaseAuthentication):
    """Bearer JWT authentication enforcing active account status and token_version match."""

    keyword = 'Bearer'

    def authenticate(self, request):
        auth_header = get_authorization_header(request).split()
        if not auth_header:
            return None

        if len(auth_header) != 2 or auth_header[0].decode('utf-8', errors='ignore').lower() != 'bearer':
            raise AuthenticationFailed('Encabezado de autorización Bearer inválido.')

        raw_token = auth_header[1].decode('utf-8', errors='ignore')
        payload = decode_access_token(raw_token)

        user_id = payload.get('user_id')
        user = Usuario.objects.filter(id_usuario=user_id).first()
        if user is None:
            raise AuthenticationFailed('Usuario no encontrado.')

        if user.estado_cuenta == AccountStatus.SUSPENDIDA or user.token_version != payload.get('token_version'):
            raise AuthenticationFailed('Sesión inválida o cuenta suspendida.')

        return (user, payload)

    def authenticate_header(self, request) -> str:
        return self.keyword
