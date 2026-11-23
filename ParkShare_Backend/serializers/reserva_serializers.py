from rest_framework import serializers

from ParkShare_Backend.models import Cochera, Reserva, Usuario


class ReservaCreateSerializer(serializers.Serializer):
    """Input serializer for creating a new parking reservation (CU-05)."""

    cochera_id = serializers.IntegerField(min_value=1)
    fecha_hora_inicio = serializers.DateTimeField()
    fecha_hora_fin = serializers.DateTimeField()


class ReservaCreationResponseSerializer(serializers.ModelSerializer):
    """Output serializer for POST /reservas creation response."""

    arrendatario_id = serializers.IntegerField(read_only=True)
    cochera_id = serializers.IntegerField(read_only=True)
    monto_total = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)

    class Meta:
        model = Reserva
        fields = [
            'id_reserva',
            'arrendatario_id',
            'cochera_id',
            'fecha_hora_inicio',
            'fecha_hora_fin',
            'hora_salida_real',
            'monto_base',
            'monto_penalizacion',
            'monto_total',
            'estado',
            'fecha_creacion',
        ]


class ReservaGarageSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Cochera
        fields = ['id_cochera', 'nombre', 'direccion']


class ReservaTenantSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Usuario
        fields = ['id_usuario', 'nombre_completo', 'placas', 'modelo_vehiculo']


class ReservaListSerializer(serializers.ModelSerializer):
    """Output serializer for role-segmented reservation lists (GET /reservas)."""

    cochera = ReservaGarageSummarySerializer(read_only=True)
    arrendatario = ReservaTenantSummarySerializer(read_only=True)
    monto_total = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)

    class Meta:
        model = Reserva
        fields = [
            'id_reserva',
            'cochera',
            'arrendatario',
            'fecha_hora_inicio',
            'fecha_hora_fin',
            'estado',
            'monto_base',
            'monto_penalizacion',
            'monto_total',
        ]


class ReservaDetailGarageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cochera
        fields = ['id_cochera', 'nombre', 'direccion', 'tarifa_hora']


class ReservaAmountBreakdownSerializer(serializers.Serializer):
    monto_base = serializers.DecimalField(max_digits=8, decimal_places=2)
    monto_penalizacion = serializers.DecimalField(max_digits=8, decimal_places=2)
    monto_total = serializers.DecimalField(max_digits=8, decimal_places=2)
    recargo_estimado_actual = serializers.DecimalField(max_digits=8, decimal_places=2)


class ReservaUsageTimeSerializer(serializers.Serializer):
    tiempo_restante_minutos = serializers.IntegerField()
    en_tolerancia = serializers.BooleanField()
    minutos_retraso_actual = serializers.IntegerField()


class ReservaDetailSerializer(serializers.ModelSerializer):
    """Output serializer for reservation detail including live usage/delay calculations."""

    desglose_montos = serializers.SerializerMethodField()
    tiempo_en_uso = serializers.SerializerMethodField()
    cochera = ReservaDetailGarageSerializer(read_only=True)
    arrendatario = ReservaTenantSummarySerializer(read_only=True)

    class Meta:
        model = Reserva
        fields = [
            'id_reserva',
            'estado',
            'fecha_hora_inicio',
            'fecha_hora_fin',
            'hora_salida_real',
            'fecha_creacion',
            'desglose_montos',
            'tiempo_en_uso',
            'cochera',
            'arrendatario',
        ]

    def get_desglose_montos(self, obj: Reserva) -> dict:
        breakdown = self.context['metrics']['desglose_montos']
        return ReservaAmountBreakdownSerializer(breakdown).data

    def get_tiempo_en_uso(self, obj: Reserva):
        usage = self.context['metrics']['tiempo_en_uso']
        if usage is None:
            return None
        return ReservaUsageTimeSerializer(usage).data


class ReservaStateUpdateSerializer(serializers.Serializer):
    """Input serializer for controlled reservation state transitions (PATCH /reservas/{id})."""

    estado = serializers.CharField()
    hora_salida_real = serializers.DateTimeField(required=False, allow_null=True)


class ReservaPatchResponseSerializer(serializers.ModelSerializer):
    """Output serializer for PATCH /reservas/{id} response."""

    monto_total = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)

    class Meta:
        model = Reserva
        fields = [
            'id_reserva',
            'estado',
            'fecha_hora_inicio',
            'fecha_hora_fin',
            'hora_salida_real',
            'monto_base',
            'monto_penalizacion',
            'monto_total',
        ]

