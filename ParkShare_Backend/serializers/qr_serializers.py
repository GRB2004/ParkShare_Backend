from rest_framework import serializers

from ParkShare_Backend.models import Cochera, CodigoQR, Reserva, Usuario
from ParkShare_Backend.services.reserva_service import compute_accumulated_penalty


class QRReservationGarageSerializer(serializers.ModelSerializer):
    """Garage summary embedded inside GET /reservas/{id}/qr for ReservationQrScreen.jsx."""

    latitud = serializers.FloatField(read_only=True)
    longitud = serializers.FloatField(read_only=True)

    class Meta:
        model = Cochera
        fields = ['id_cochera', 'nombre', 'direccion', 'latitud', 'longitud']


class QRReservationTenantSerializer(serializers.ModelSerializer):
    """Tenant summary embedded inside GET /reservas/{id}/qr for QrDisplayCard.jsx."""

    class Meta:
        model = Usuario
        fields = ['id_usuario', 'nombre_completo', 'placas', 'modelo_vehiculo']


class QRReservationSummarySerializer(serializers.ModelSerializer):
    cochera = QRReservationGarageSerializer(read_only=True)
    arrendatario = QRReservationTenantSerializer(read_only=True)
    multa_acumulada = serializers.SerializerMethodField()

    class Meta:
        model = Reserva
        fields = [
            'id_reserva',
            'estado',
            'fecha_hora_inicio',
            'fecha_hora_fin',
            'monto_base',
            'monto_penalizacion',
            'multa_acumulada',
            'cochera',
            'arrendatario',
        ]

    def get_multa_acumulada(self, obj: Reserva) -> str:
        return f'{compute_accumulated_penalty(obj):.2f}'


class CodigoQRDetailSerializer(serializers.ModelSerializer):
    """Output serializer for active reservation QR token (`GET /reservas/{id}/qr`)."""

    reserva_id = serializers.IntegerField(read_only=True)
    reserva = QRReservationSummarySerializer(read_only=True)

    class Meta:
        model = CodigoQR
        fields = [
            'id_qr',
            'reserva_id',
            'token_qr',
            'tipo_qr',
            'usado',
            'fecha_emision',
            'expiracion',
            'reserva',
        ]


class ValidateQRInputSerializer(serializers.Serializer):
    """Input serializer for scanning an entry or exit QR token (`POST /reservas/validar`)."""

    token = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)


class ActiveSimulationQRSerializer(serializers.ModelSerializer):
    """Output serializer for GET /reservas/simular-qr matching frontend MockQrDemoOption."""

    reserva_id = serializers.IntegerField(read_only=True)
    cochera_nombre = serializers.CharField(source='reserva.cochera.nombre', read_only=True)
    placas = serializers.CharField(source='reserva.arrendatario.placas', read_only=True)
    modelo_vehiculo = serializers.CharField(
        source='reserva.arrendatario.modelo_vehiculo', read_only=True
    )
    nombre_arrendatario = serializers.CharField(
        source='reserva.arrendatario.nombre_completo', read_only=True
    )
    estado_reserva = serializers.CharField(source='reserva.estado', read_only=True)
    fecha_hora_inicio = serializers.DateTimeField(
        source='reserva.fecha_hora_inicio', read_only=True
    )
    fecha_hora_fin = serializers.DateTimeField(
        source='reserva.fecha_hora_fin', read_only=True
    )

    class Meta:
        model = CodigoQR
        fields = [
            'id_qr',
            'reserva_id',
            'tipo_qr',
            'token_qr',
            'cochera_nombre',
            'placas',
            'modelo_vehiculo',
            'nombre_arrendatario',
            'estado_reserva',
            'fecha_hora_inicio',
            'fecha_hora_fin',
            'expiracion',
        ]


class SimulateQRInputSerializer(serializers.Serializer):
    """Input serializer for POST /reservas/simular-qr."""

    reserva_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        error_messages={
            'invalid': 'El identificador de la reserva debe ser un número entero válido.',
            'min_value': 'El identificador de la reserva debe ser mayor a cero.',
        },
    )
    tipo_qr = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=15,
    )
    token = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        trim_whitespace=True,
        max_length=100,
    )
    respetar_ventana = serializers.BooleanField(required=False, default=False)
