from rest_framework import serializers


class ProcessPaymentSerializer(serializers.Serializer):
    """Input serializer for POST /pagos (CU-07)."""

    reserva_id = serializers.IntegerField(
        required=True,
        min_value=1,
        error_messages={
            'required': 'El identificador de la reservación es obligatorio.',
            'invalid': 'El identificador de la reservación debe ser un número entero válido.',
            'min_value': 'El identificador de la reservación debe ser mayor a cero.',
        },
    )
    metodo = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=50,
        error_messages={
            'required': 'El método de pago es obligatorio.',
            'blank': 'El método de pago no puede estar vacío.',
            'max_length': 'El método de pago no puede exceder 50 caracteres.',
        },
    )
    token_pago = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=150,
    )


class PagoResponseSerializer(serializers.Serializer):
    """Output serializer for POST /pagos (201 Created / 402 Payment Required)."""

    id_pago = serializers.IntegerField()
    reserva_id = serializers.IntegerField()
    monto_total = serializers.CharField()
    metodo = serializers.CharField()
    referencia_pasarela = serializers.CharField(allow_null=True)
    estado = serializers.CharField()
    fecha_pago = serializers.DateTimeField()
    estado_reserva = serializers.CharField()
    qr_entrada_generado = serializers.BooleanField()
    mensaje = serializers.CharField(required=False)
    detail = serializers.CharField(required=False)


class HostFinancialMovementSerializer(serializers.Serializer):
    """Serializer for a single movement in GET /arrendador/cobros."""

    id_pago = serializers.IntegerField()
    reserva_id = serializers.IntegerField()
    cochera = serializers.CharField()
    fecha_horario = serializers.CharField()
    monto = serializers.CharField()
    monto_base = serializers.CharField()
    monto_penalizacion = serializers.CharField()
    multa_acumulada = serializers.CharField(required=False)
    estado = serializers.CharField()
    estado_reserva = serializers.CharField(required=False)
    fecha_pago = serializers.DateTimeField()


class HostFinancialSummarySerializer(serializers.Serializer):
    """Output serializer for GET /arrendador/cobros."""

    ganado_hoy = serializers.CharField()
    disponible_para_retirar = serializers.CharField()
    multa_acumulada = serializers.CharField(required=False)
    cuenta_cobro = serializers.CharField(allow_null=True)
    movimientos = HostFinancialMovementSerializer(many=True)


class WithdrawBalanceSerializer(serializers.Serializer):
    """Input serializer for POST /arrendador/cobros/retirar."""

    cuenta_cobro = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=100,
    )
