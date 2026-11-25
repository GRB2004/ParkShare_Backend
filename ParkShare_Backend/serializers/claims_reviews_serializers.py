from rest_framework import serializers

from ParkShare_Backend.models import Queja, Resena


class CreateReviewInputSerializer(serializers.Serializer):
    """Input serializer for POST /reservas/{id}/resenas (CU-09)."""

    calificacion = serializers.IntegerField(
        required=True,
        min_value=1,
        max_value=5,
        error_messages={
            'required': 'La calificación es obligatoria.',
            'invalid': 'La calificación debe ser un número entero entre 1 y 5.',
            'min_value': 'La calificación mínima es 1 estrella.',
            'max_value': 'La calificación máxima es 5 estrellas.',
        },
    )
    etiquetas = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=255,
        error_messages={
            'max_length': 'Las etiquetas no pueden exceder 255 caracteres.',
        },
    )
    comentario = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    def validate_calificacion(self, value: int) -> int:
        raw_val = self.initial_data.get('calificacion') if isinstance(self.initial_data, dict) else None
        if isinstance(raw_val, bool):
            raise serializers.ValidationError('La calificación debe ser un número entero entre 1 y 5.')
        return value


class ResenaResponseSerializer(serializers.ModelSerializer):
    """Output serializer for POST /reservas/{id}/resenas."""

    reserva_id = serializers.IntegerField(read_only=True)
    autor_id = serializers.IntegerField(read_only=True)
    destinatario_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Resena
        fields = [
            'id_resena',
            'reserva_id',
            'autor_id',
            'destinatario_id',
            'calificacion',
            'etiquetas',
            'comentario',
            'fecha',
        ]


class CreateComplaintInputSerializer(serializers.Serializer):
    """Input serializer for POST /quejas (CU-12)."""

    reserva_id = serializers.IntegerField(
        required=True,
        min_value=1,
        error_messages={
            'required': 'El identificador de la reservación es obligatorio.',
            'invalid': 'El identificador de la reservación debe ser un número entero válido.',
            'min_value': 'El identificador de la reservación debe ser mayor a cero.',
        },
    )
    motivo_categoria = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=30,
        error_messages={
            'required': 'La categoría del motivo es obligatoria.',
            'blank': 'La categoría del motivo no puede estar vacía.',
        },
    )
    descripcion = serializers.CharField(
        required=True,
        allow_blank=False,
        error_messages={
            'required': 'La descripción de la queja es obligatoria.',
            'blank': 'La descripción de la queja no puede estar vacía.',
        },
    )


class QuejaResponseSerializer(serializers.ModelSerializer):
    """Output serializer for POST /quejas."""

    reserva_id = serializers.IntegerField(read_only=True)
    arrendatario_id = serializers.IntegerField(read_only=True)
    arrendador_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Queja
        fields = [
            'id_queja',
            'reserva_id',
            'arrendatario_id',
            'arrendador_id',
            'motivo_categoria',
            'descripcion',
            'estado',
            'fecha_creacion',
        ]


class ResolveComplaintInputSerializer(serializers.Serializer):
    """Input serializer for PATCH /admin/quejas/{id} (CU-13)."""

    estado = serializers.CharField(required=True, allow_blank=False)
    motivo_sancion = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )


class SubmitAppealInputSerializer(serializers.Serializer):
    """Input serializer for POST /sanciones/{id}/apelar (CU-13)."""

    descripcion = serializers.CharField(
        required=True,
        allow_blank=False,
        error_messages={
            'required': 'La descripción de la apelación es obligatoria.',
            'blank': 'La descripción de la apelación no puede estar vacía.',
        },
    )
    url_evidencia = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )


class ResolveAppealInputSerializer(serializers.Serializer):
    """Input serializer for PATCH /admin/apelaciones/{id} (CU-13)."""

    estado = serializers.CharField(required=True, allow_blank=False)
