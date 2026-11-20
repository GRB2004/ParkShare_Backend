from rest_framework import serializers

from ParkShare_Backend.models import (
    DocumentoArrendador,
    SuspensionCuenta,
    UserRole,
    Usuario,
)


class RegisterSerializer(serializers.Serializer):
    """Input serializer for public tenant/landlord registration."""

    nombre_completo = serializers.CharField(max_length=150)
    correo = serializers.EmailField(max_length=120)
    telefono = serializers.CharField(max_length=15)
    password = serializers.CharField(write_only=True)
    fecha_nacimiento = serializers.DateField()
    rol = serializers.CharField(default=UserRole.ARRENDATARIO)
    placas = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    modelo_vehiculo = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    cuenta_cobro = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    url_ine = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    url_csf = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)
    url_propiedad = serializers.CharField(max_length=255, required=False, allow_blank=True, allow_null=True)


class LoginSerializer(serializers.Serializer):
    """Input credentials serializer for login (CU-01)."""

    correo = serializers.EmailField(max_length=120)
    password = serializers.CharField(write_only=True)


class UserBaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Usuario
        fields = [
            'id_usuario',
            'nombre_completo',
            'correo',
            'telefono',
            'fecha_nacimiento',
            'rol',
            'estado_cuenta',
        ]


class LandlordDocumentSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentoArrendador
        fields = [
            'id_documento',
            'estado_verificacion',
            'url_ine',
            'url_csf',
            'url_propiedad',
        ]


class LandlordInRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Usuario
        fields = [
            'id_usuario',
            'nombre_completo',
            'correo',
            'telefono',
            'fecha_nacimiento',
            'estado_cuenta',
        ]


class LandlordRequestListSerializer(serializers.ModelSerializer):
    arrendador = LandlordInRequestSerializer(read_only=True)

    class Meta:
        model = DocumentoArrendador
        fields = [
            'id_documento',
            'estado_verificacion',
            'arrendador',
        ]


class LandlordDetailInRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Usuario
        fields = [
            'id_usuario',
            'nombre_completo',
            'correo',
            'telefono',
            'fecha_nacimiento',
            'cuenta_cobro',
            'estado_cuenta',
        ]


class LandlordRequestDetailSerializer(serializers.ModelSerializer):
    arrendador = LandlordDetailInRequestSerializer(read_only=True)

    class Meta:
        model = DocumentoArrendador
        fields = [
            'id_documento',
            'estado_verificacion',
            'motivo_rechazo',
            'url_ine',
            'url_csf',
            'url_propiedad',
            'arrendador',
        ]


class ReviewLandlordRequestSerializer(serializers.Serializer):
    """Input serializer for administrative review of landlord requests (CU-10)."""

    estado_verificacion = serializers.CharField()
    motivo_rechazo = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    detalle_motivo = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class AdminUserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Usuario
        fields = [
            'id_usuario',
            'nombre_completo',
            'correo',
            'telefono',
            'rol',
            'estado_cuenta',
        ]


class AccountSuspensionSerializer(serializers.ModelSerializer):
    usuario_id = serializers.IntegerField(read_only=True)
    admin_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = SuspensionCuenta
        fields = [
            'id_suspension',
            'usuario_id',
            'admin_id',
            'motivo',
            'estado',
            'fecha_suspension',
        ]


class SuspendAccountSerializer(serializers.Serializer):
    """Input serializer for account suspension (CU-11)."""

    motivo = serializers.CharField(required=False, allow_blank=True, default='')


def serialize_registered_user(user: Usuario) -> dict:
    """Build role-specific profile payload upon successful registration."""
    data = UserBaseSerializer(user).data
    if user.rol == UserRole.ARRENDATARIO:
        data['placas'] = user.placas
        data['modelo_vehiculo'] = user.modelo_vehiculo
    elif user.rol == UserRole.ARRENDADOR:
        data['cuenta_cobro'] = user.cuenta_cobro
    return data
