import os
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone


ALPHABETIC_NAME_REGEX = re.compile(r'^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s]+$')
PHONE_REGEX = re.compile(r'^(?:\d{10}|\+[1-9]\d{9,14})$')

INE_ALLOWED_EXTENSIONS = ('.pdf', '.jpg', '.jpeg', '.png')
PDF_ONLY_EXTENSIONS = ('.pdf',)
GARAGE_PHOTO_ALLOWED_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp')


def validate_alphabetic_name(value: str) -> None:
    """Valida que el nombre contenga únicamente letras y espacios."""
    cleaned = (value or '').strip()
    if not cleaned or not ALPHABETIC_NAME_REGEX.match(cleaned):
        raise ValidationError('El nombre completo solo puede contener letras y espacios.')


def validate_phone_number(value: str) -> None:
    """Valida teléfono a 10 dígitos numéricos o en formato E.164."""
    cleaned = (value or '').strip()
    if not PHONE_REGEX.match(cleaned):
        raise ValidationError('El teléfono debe tener 10 dígitos numéricos o formato internacional E.164.')


def validate_adult_birth_date(value) -> None:
    """Valida que la persona tenga al menos 18 años cumplidos en horario de México."""
    if value is None:
        raise ValidationError('La fecha de nacimiento es obligatoria.')
    today = timezone.now().astimezone(ZoneInfo('America/Mexico_City')).date()
    age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
    if age < 18:
        raise ValidationError('El usuario debe ser mayor de 18 años.')


def validate_landlord_gmail(email: str) -> None:
    """Valida que el correo electrónico del arrendador pertenezca al dominio @gmail.com (RN-07)."""
    normalized = (email or '').strip().lower()
    if not normalized.endswith('@gmail.com'):
        raise ValidationError('El arrendador debe registrarse con un correo electrónico @gmail.com.')


def validate_file_extension(value: str, allowed_extensions: tuple[str, ...]) -> None:
    """Valida que la ruta o URL del documento tenga una extensión permitida."""
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValidationError('La URL o ruta del documento es obligatoria.')
    parsed_path = urlparse(cleaned).path or cleaned
    _, ext = os.path.splitext(parsed_path)
    if ext.lower() not in allowed_extensions:
        allowed_str = ', '.join(allowed_extensions)
        raise ValidationError(f'Formato de archivo no permitido. Extensiones válidas: {allowed_str}.')


def validate_ine_extension(value: str) -> None:
    """Valida extensión permitida para identificación oficial INE (.pdf, .jpg, .jpeg, .png)."""
    validate_file_extension(value, INE_ALLOWED_EXTENSIONS)


def validate_pdf_extension(value: str) -> None:
    """Valida extensión exclusivamente PDF para constancia fiscal y propiedad."""
    validate_file_extension(value, PDF_ONLY_EXTENSIONS)


def validate_garage_photo_extension(value: str) -> None:
    """Valida extensión de imagen permitida para fotografías de cochera (.jpg, .jpeg, .png, .webp)."""
    validate_file_extension(value, GARAGE_PHOTO_ALLOWED_EXTENSIONS)

