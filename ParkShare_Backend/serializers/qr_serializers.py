from rest_framework import serializers

from ParkShare_Backend.models import CodigoQR, Reserva


class QRReservationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Reserva
        fields = ['id_reserva', 'estado', 'fecha_hora_inicio', 'fecha_hora_fin']


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
