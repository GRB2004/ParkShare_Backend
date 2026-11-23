from decimal import Decimal

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from .validators import (
    validate_adult_birth_date,
    validate_alphabetic_name,
    validate_garage_photo_extension,
    validate_ine_extension,
    validate_landlord_gmail,
    validate_pdf_extension,
    validate_phone_number,
    validate_sha256_hex_token,
)


class UserRole(models.TextChoices):
    ARRENDATARIO = 'ARRENDATARIO', 'Arrendatario'
    ARRENDADOR = 'ARRENDADOR', 'Arrendador'
    ADMIN = 'ADMIN', 'Administrador'


class AccountStatus(models.TextChoices):
    ACTIVA = 'ACTIVA', 'Activa'
    PENDIENTE = 'PENDIENTE', 'Pendiente'
    RECHAZADA = 'RECHAZADA', 'Rechazada'
    SUSPENDIDA = 'SUSPENDIDA', 'Suspendida'


class VerificationStatus(models.TextChoices):
    PENDIENTE = 'PENDIENTE', 'Pendiente'
    APROBADO = 'APROBADO', 'Aprobado'
    RECHAZADO = 'RECHAZADO', 'Rechazado'


class SuspensionStatus(models.TextChoices):
    ACTIVA = 'ACTIVA', 'Activa'
    REVOCADA = 'REVOCADA', 'Revocada'


class VehicleType(models.TextChoices):
    AUTO = 'AUTO', 'Auto'
    MOTO = 'MOTO', 'Moto'
    AMBOS = 'AMBOS', 'Ambos'


class GarageStatus(models.TextChoices):
    PENDIENTE = 'PENDIENTE', 'Pendiente'
    APROBADA = 'APROBADA', 'Aprobada'
    RECHAZADA = 'RECHAZADA', 'Rechazada'
    INACTIVA = 'INACTIVA', 'Inactiva'


class WeekdayChoice(models.TextChoices):
    L = 'L', 'Lunes'
    M = 'M', 'Martes'
    X = 'X', 'Miércoles'
    J = 'J', 'Jueves'
    V = 'V', 'Viernes'
    S = 'S', 'Sábado'
    D = 'D', 'Domingo'


class ReservationStatus(models.TextChoices):
    SOLICITADA = 'SOLICITADA', 'Solicitada'
    CONFIRMADA = 'CONFIRMADA', 'Confirmada'
    EN_USO = 'EN_USO', 'En uso'
    FINALIZADA = 'FINALIZADA', 'Finalizada'
    CANCELADA = 'CANCELADA', 'Cancelada'
    EXPIRADA = 'EXPIRADA', 'Expirada'


class QRCodeType(models.TextChoices):
    ENTRADA = 'ENTRADA', 'Entrada'
    SALIDA = 'SALIDA', 'Salida'


class UsuarioManager(BaseUserManager):
    """Custom manager for ParkShare users supporting CLI superuser creation."""

    def create_user(
        self,
        nombre_completo: str,
        correo: str,
        telefono: str,
        password: str | None = None,
        fecha_nacimiento=None,
        rol: str = UserRole.ARRENDATARIO,
        **extra_fields,
    ):
        if not correo:
            raise ValueError('El correo electrónico es obligatorio.')
        if not password:
            raise ValueError('La contraseña es obligatoria.')

        normalized_email = self.normalize_email(correo).strip().lower()
        default_status = (
            AccountStatus.PENDIENTE if rol == UserRole.ARRENDADOR else AccountStatus.ACTIVA
        )
        account_status = extra_fields.pop('estado_cuenta', default_status)

        user = self.model(
            nombre_completo=nombre_completo.strip() if nombre_completo else '',
            correo=normalized_email,
            telefono=telefono.strip() if telefono else '',
            fecha_nacimiento=fecha_nacimiento,
            rol=rol,
            estado_cuenta=account_status,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        correo: str,
        nombre_completo: str,
        telefono: str,
        fecha_nacimiento,
        password: str | None = None,
        **extra_fields,
    ):
        extra_fields.setdefault('rol', UserRole.ADMIN)
        extra_fields.setdefault('estado_cuenta', AccountStatus.ACTIVA)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('El superusuario debe tener is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('El superusuario debe tener is_superuser=True.')

        return self.create_user(
            nombre_completo=nombre_completo,
            correo=correo,
            telefono=telefono,
            password=password,
            fecha_nacimiento=fecha_nacimiento,
            **extra_fields,
        )


class Usuario(AbstractBaseUser, PermissionsMixin):
    """ParkShare domain user model mapped to the `usuario` database table."""

    id_usuario = models.BigAutoField(primary_key=True, db_column='id_usuario')
    nombre_completo = models.CharField(
        max_length=150,
        db_column='nombre_completo',
        validators=[validate_alphabetic_name],
    )
    correo = models.EmailField(
        max_length=120,
        unique=True,
        db_column='correo',
    )
    telefono = models.CharField(
        max_length=15,
        db_column='telefono',
        validators=[validate_phone_number],
    )
    password = models.CharField(max_length=255, db_column='password_hash')
    fecha_nacimiento = models.DateField(
        db_column='fecha_nacimiento',
        validators=[validate_adult_birth_date],
    )
    rol = models.CharField(
        max_length=15,
        choices=UserRole.choices,
        default=UserRole.ARRENDATARIO,
        db_column='rol',
        db_index=True,
    )
    estado_cuenta = models.CharField(
        max_length=15,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVA,
        db_column='estado_cuenta',
        db_index=True,
    )
    placas = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        db_column='placas',
    )
    modelo_vehiculo = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_column='modelo_vehiculo',
    )
    cuenta_cobro = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        db_column='cuenta_cobro',
    )
    token_version = models.IntegerField(default=1, db_column='token_version')
    is_staff = models.BooleanField(default=False, db_column='is_staff')
    is_active = models.BooleanField(default=True, db_column='is_active')

    objects = UsuarioManager()

    USERNAME_FIELD = 'correo'
    REQUIRED_FIELDS = ['nombre_completo', 'telefono', 'fecha_nacimiento']

    class Meta:
        db_table = 'usuario'
        indexes = [
            models.Index(fields=['rol', 'estado_cuenta'], name='idx_usuario_rol_estado'),
            models.Index(fields=['nombre_completo'], name='idx_usuario_nombre'),
        ]

    def __init__(self, *args, **kwargs):
        has_explicit_status = 'estado_cuenta' in kwargs
        super().__init__(*args, **kwargs)
        if not args and kwargs and not has_explicit_status and self.rol == UserRole.ARRENDADOR:
            self.estado_cuenta = AccountStatus.PENDIENTE

    def __str__(self) -> str:
        return f'{self.nombre_completo} ({self.correo})'

    @property
    def password_hash(self) -> str:
        return self.password

    @password_hash.setter
    def password_hash(self, value: str) -> None:
        self.password = value

    def clean(self) -> None:
        super().clean()
        if self.correo:
            self.correo = self.correo.strip().lower()

        errors = {}
        if self.rol == UserRole.ARRENDADOR:
            try:
                validate_landlord_gmail(self.correo)
            except ValidationError as exc:
                errors['correo'] = exc.messages

        if self.rol == UserRole.ARRENDATARIO:
            if not (self.placas or '').strip():
                errors['placas'] = ['Las placas del vehículo son obligatorias para el arrendatario.']
            if not (self.modelo_vehiculo or '').strip():
                errors['modelo_vehiculo'] = ['El modelo del vehículo es obligatorio para el arrendatario.']

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        if self.correo:
            self.correo = self.correo.strip().lower()
        self.is_staff = self.rol == UserRole.ADMIN or self.is_superuser
        self.is_active = self.estado_cuenta != AccountStatus.SUSPENDIDA
        super().save(*args, **kwargs)


class DocumentoArrendador(models.Model):
    """Landlord verification dossier subject to admin review (CU-10)."""

    id_documento = models.BigAutoField(primary_key=True, db_column='id_documento')
    arrendador = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name='landlord_documents',
        db_column='arrendador_id',
    )
    url_ine = models.CharField(
        max_length=255,
        db_column='url_ine',
        validators=[validate_ine_extension],
    )
    url_csf = models.CharField(
        max_length=255,
        db_column='url_csf',
        validators=[validate_pdf_extension],
    )
    url_propiedad = models.CharField(
        max_length=255,
        db_column='url_propiedad',
        validators=[validate_pdf_extension],
    )
    estado_verificacion = models.CharField(
        max_length=15,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDIENTE,
        db_column='estado_verificacion',
        db_index=True,
    )
    motivo_rechazo = models.TextField(
        null=True,
        blank=True,
        db_column='motivo_rechazo',
    )

    class Meta:
        db_table = 'documento_arrendador'

    def __str__(self) -> str:
        return f'Document #{self.id_documento} - {self.arrendador_id} ({self.estado_verificacion})'

    def clean(self) -> None:
        super().clean()
        if self.estado_verificacion == VerificationStatus.RECHAZADO and not (self.motivo_rechazo or '').strip():
            raise ValidationError({
                'motivo_rechazo': ['El motivo de rechazo es obligatorio al rechazar la solicitud.']
            })


class SuspensionCuenta(models.Model):
    """Administrative account suspension record (CU-11)."""

    id_suspension = models.BigAutoField(primary_key=True, db_column='id_suspension')
    usuario = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name='received_suspensions',
        db_column='usuario_id',
    )
    admin = models.ForeignKey(
        Usuario,
        on_delete=models.RESTRICT,
        related_name='issued_suspensions',
        db_column='admin_id',
    )
    motivo = models.TextField(db_column='motivo')
    estado = models.CharField(
        max_length=15,
        choices=SuspensionStatus.choices,
        default=SuspensionStatus.ACTIVA,
        db_column='estado',
    )
    fecha_suspension = models.DateTimeField(
        auto_now_add=True,
        db_column='fecha_suspension',
    )

    class Meta:
        db_table = 'suspension_cuenta'

    def __str__(self) -> str:
        return f'Suspension #{self.id_suspension} - user {self.usuario_id} ({self.estado})'

    def clean(self) -> None:
        super().clean()
        if not (self.motivo or '').strip():
            raise ValidationError({'motivo': ['El motivo de suspensión es obligatorio.']})


class Cochera(models.Model):
    """Parking garage listing published by an active landlord (CU-02 / RN-09 / RN-10)."""

    id_cochera = models.BigAutoField(primary_key=True, db_column='id_cochera')
    arrendador = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name='garages',
        db_column='arrendador_id',
    )
    nombre = models.CharField(max_length=120, db_column='nombre')
    direccion = models.CharField(max_length=255, db_column='direccion')
    latitud = models.DecimalField(
        max_digits=10,
        decimal_places=8,
        db_column='latitud',
        validators=[
            MinValueValidator(Decimal('-90.00000000')),
            MaxValueValidator(Decimal('90.00000000')),
        ],
    )
    longitud = models.DecimalField(
        max_digits=11,
        decimal_places=8,
        db_column='longitud',
        validators=[
            MinValueValidator(Decimal('-180.00000000')),
            MaxValueValidator(Decimal('180.00000000')),
        ],
    )
    metros_cuadrados = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        db_column='metros_cuadrados',
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    espacios_totales = models.IntegerField(
        db_column='espacios_totales',
        validators=[MinValueValidator(1)],
    )
    tarifa_hora = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        db_column='tarifa_hora',
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    tipo_vehiculo = models.CharField(
        max_length=10,
        choices=VehicleType.choices,
        default=VehicleType.AMBOS,
        db_column='tipo_vehiculo',
    )
    estado_cochera = models.CharField(
        max_length=15,
        choices=GarageStatus.choices,
        default=GarageStatus.PENDIENTE,
        db_column='estado_cochera',
        db_index=True,
    )

    class Meta:
        db_table = 'cochera'
        indexes = [
            models.Index(fields=['latitud', 'longitud'], name='idx_cochera_lat_lng'),
        ]

    def __str__(self) -> str:
        return f'{self.nombre} ({self.estado_cochera})'

    def clean(self) -> None:
        super().clean()
        errors = {}
        if not (self.nombre or '').strip():
            errors['nombre'] = ['El nombre de la cochera es obligatorio.']
        if not (self.direccion or '').strip():
            errors['direccion'] = ['La dirección de la cochera es obligatoria.']
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        if self.nombre:
            self.nombre = self.nombre.strip()
        if self.direccion:
            self.direccion = self.direccion.strip()
        super().save(*args, **kwargs)


class FotoCochera(models.Model):
    """Garage photograph record validating image extensions (RN-09)."""

    id_foto = models.BigAutoField(primary_key=True, db_column='id_foto')
    cochera = models.ForeignKey(
        Cochera,
        on_delete=models.CASCADE,
        related_name='fotos',
        db_column='cochera_id',
    )
    url_foto = models.CharField(
        max_length=255,
        db_column='url_foto',
        validators=[validate_garage_photo_extension],
    )

    class Meta:
        db_table = 'foto_cochera'

    def __str__(self) -> str:
        return f'Photo #{self.id_foto} - garage {self.cochera_id}'

    def clean(self) -> None:
        if self.url_foto:
            self.url_foto = self.url_foto.strip()
        super().clean()


class Disponibilidad(models.Model):
    """Weekly operating schedule window for a garage (CU-03)."""

    id_disponibilidad = models.BigAutoField(primary_key=True, db_column='id_disponibilidad')
    cochera = models.ForeignKey(
        Cochera,
        on_delete=models.CASCADE,
        related_name='disponibilidades',
        db_column='cochera_id',
    )
    dia_semana = models.CharField(
        max_length=2,
        choices=WeekdayChoice.choices,
        db_column='dia_semana',
        db_index=True,
    )
    hora_inicio = models.TimeField(db_column='hora_inicio')
    hora_fin = models.TimeField(db_column='hora_fin')

    class Meta:
        db_table = 'disponibilidad'

    def __str__(self) -> str:
        return f'{self.cochera_id} {self.dia_semana} {self.hora_inicio}-{self.hora_fin}'

    def clean(self) -> None:
        super().clean()
        if self.hora_inicio is not None and self.hora_fin is not None and self.hora_fin <= self.hora_inicio:
            raise ValidationError({
                'hora_fin': ['La hora de cierre debe ser posterior a la hora de apertura.'],
            })


class Reserva(models.Model):
    """Parking reservation lifecycle, state machine, and penalty tracking (CU-05 / RN-01..RN-05)."""

    id_reserva = models.BigAutoField(primary_key=True, db_column='id_reserva')
    arrendatario = models.ForeignKey(
        Usuario,
        on_delete=models.RESTRICT,
        related_name='reservations',
        db_column='arrendatario_id',
    )
    cochera = models.ForeignKey(
        Cochera,
        on_delete=models.RESTRICT,
        related_name='reservations',
        db_column='cochera_id',
    )
    fecha_hora_inicio = models.DateTimeField(db_column='fecha_hora_inicio')
    fecha_hora_fin = models.DateTimeField(db_column='fecha_hora_fin')
    hora_salida_real = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        db_column='hora_salida_real',
    )
    monto_base = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        db_column='monto_base',
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    monto_penalizacion = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0.00'),
        db_column='monto_penalizacion',
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    estado = models.CharField(
        max_length=15,
        choices=ReservationStatus.choices,
        default=ReservationStatus.SOLICITADA,
        db_column='estado',
        db_index=True,
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True, db_column='fecha_creacion')

    class Meta:
        db_table = 'reserva'
        indexes = [
            models.Index(
                fields=['cochera', 'estado', 'fecha_hora_inicio', 'fecha_hora_fin'],
                name='idx_reserva_cupo',
            ),
            models.Index(
                fields=['arrendatario', 'estado', 'fecha_hora_inicio'],
                name='idx_reserva_arrendatario',
            ),
        ]

    def __str__(self) -> str:
        return f'Reservation #{self.id_reserva} - garage {self.cochera_id} ({self.estado})'

    @property
    def monto_total(self) -> Decimal:
        base = self.monto_base if self.monto_base is not None else Decimal('0.00')
        penalty = self.monto_penalizacion if self.monto_penalizacion is not None else Decimal('0.00')
        return base + penalty

    def clean(self) -> None:
        super().clean()
        if (
            self.fecha_hora_inicio is not None
            and self.fecha_hora_fin is not None
            and self.fecha_hora_fin <= self.fecha_hora_inicio
        ):
            raise ValidationError({
                'fecha_hora_fin': ['La fecha y hora de fin debe ser posterior a la fecha y hora de inicio.'],
            })


class CodigoQR(models.Model):
    """Single-use cryptographic SHA-256 access/checkout QR token linked to a reservation."""

    id_qr = models.BigAutoField(primary_key=True, db_column='id_qr')
    reserva = models.ForeignKey(
        Reserva,
        on_delete=models.CASCADE,
        related_name='codigos_qr',
        db_column='reserva_id',
    )
    token_qr = models.CharField(
        max_length=64,
        unique=True,
        db_column='token_qr',
        validators=[validate_sha256_hex_token],
    )
    tipo_qr = models.CharField(
        max_length=10,
        choices=QRCodeType.choices,
        db_column='tipo_qr',
    )
    usado = models.BooleanField(default=False, db_column='usado')
    fecha_emision = models.DateTimeField(default=timezone.now, db_column='fecha_emision')
    expiracion = models.DateTimeField(db_column='expiracion', db_index=True)

    class Meta:
        db_table = 'codigo_qr'
        constraints = [
            models.UniqueConstraint(
                fields=['reserva', 'tipo_qr'],
                name='uq_codigo_qr_reserva_tipo',
            ),
        ]
        indexes = [
            models.Index(
                fields=['reserva', 'tipo_qr', 'usado'],
                name='idx_qr_reserva_tipo_usado',
            ),
        ]

    def __str__(self) -> str:
        return f'QR #{self.id_qr} - reservation {self.reserva_id} ({self.tipo_qr})'

    def clean(self) -> None:
        if isinstance(self.token_qr, str):
            self.token_qr = self.token_qr.strip().lower()
        super().clean()
        if (
            self.fecha_emision is not None
            and self.expiracion is not None
            and self.expiracion <= self.fecha_emision
        ):
            raise ValidationError({
                'expiracion': ['La fecha de expiración debe ser posterior a la fecha de emisión.'],
            })

    def save(self, *args, **kwargs) -> None:
        if isinstance(self.token_qr, str):
            self.token_qr = self.token_qr.strip().lower()
        super().save(*args, **kwargs)
