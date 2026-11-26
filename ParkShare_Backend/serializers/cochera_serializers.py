from rest_framework import serializers

from ParkShare_Backend.models import (
    Cochera,
    Disponibilidad,
    FotoCochera,
    Usuario,
    VehicleType,
)
from ParkShare_Backend.services.cochera_service import get_garage_rating_summary


def _get_primary_photo_url(garage: Cochera) -> str | None:
    """Return the first prefetched photo URL without triggering an extra SQL query."""
    photos = list(garage.fotos.all())
    return photos[0].url_foto if photos else None


class FotoCocheraSerializer(serializers.ModelSerializer):
    class Meta:
        model = FotoCochera
        fields = ['id_foto', 'url_foto']


class DisponibilidadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Disponibilidad
        fields = ['id_disponibilidad', 'dia_semana', 'hora_inicio', 'hora_fin']


class LandlordSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Usuario
        fields = ['id_usuario', 'nombre_completo']


class CocheraCreateSerializer(serializers.Serializer):
    """Input serializer for creating a new garage listing (CU-02 / CU-03 / RN-09)."""

    nombre = serializers.CharField(max_length=120)
    direccion = serializers.CharField(max_length=255)
    latitud = serializers.DecimalField(max_digits=10, decimal_places=8)
    longitud = serializers.DecimalField(max_digits=11, decimal_places=8)
    metros_cuadrados = serializers.DecimalField(max_digits=6, decimal_places=2)
    espacios_totales = serializers.IntegerField()
    tarifa_hora = serializers.DecimalField(max_digits=8, decimal_places=2)
    tipo_vehiculo = serializers.CharField(required=False, default=VehicleType.AMBOS)
    fotos = serializers.ListField(allow_empty=True, required=True)
    disponibilidades = serializers.ListField(allow_empty=True, required=True)


class CocheraUpdateSerializer(serializers.Serializer):
    """Input serializer for partial landlord garage updates and status toggles."""

    nombre = serializers.CharField(max_length=120, required=False)
    tarifa_hora = serializers.DecimalField(max_digits=8, decimal_places=2, required=False)
    espacios_totales = serializers.IntegerField(required=False)
    tipo_vehiculo = serializers.CharField(required=False)
    estado_cochera = serializers.CharField(required=False)
    disponibilidades = serializers.ListField(allow_empty=True, required=False)


class CocheraResponseSerializer(serializers.ModelSerializer):
    """Output serializer for newly created garage listings (POST /cocheras)."""

    arrendador_id = serializers.IntegerField(read_only=True)
    fotos = FotoCocheraSerializer(many=True, read_only=True)
    disponibilidades = DisponibilidadSerializer(many=True, read_only=True)

    class Meta:
        model = Cochera
        fields = [
            'id_cochera',
            'arrendador_id',
            'nombre',
            'direccion',
            'latitud',
            'longitud',
            'metros_cuadrados',
            'espacios_totales',
            'tarifa_hora',
            'tipo_vehiculo',
            'estado_cochera',
            'fotos',
            'disponibilidades',
        ]


class CocheraSearchResultSerializer(serializers.ModelSerializer):
    """Output serializer for geospatial and similarity garage search results (CU-04)."""

    distancia_km = serializers.FloatField(read_only=True)
    espacios_disponibles_ahora = serializers.IntegerField(read_only=True)
    abierta_ahora = serializers.BooleanField(read_only=True)
    similitud_busqueda = serializers.FloatField(read_only=True, default=0.0)
    es_recomendada = serializers.BooleanField(read_only=True, default=False)
    foto_principal = serializers.SerializerMethodField()

    class Meta:
        model = Cochera
        fields = [
            'id_cochera',
            'nombre',
            'direccion',
            'latitud',
            'longitud',
            'distancia_km',
            'tarifa_hora',
            'espacios_totales',
            'espacios_disponibles_ahora',
            'abierta_ahora',
            'tipo_vehiculo',
            'foto_principal',
            'similitud_busqueda',
            'es_recomendada',
        ]

    def get_foto_principal(self, obj: Cochera) -> str | None:
        return _get_primary_photo_url(obj)


class LandlordGarageListSerializer(serializers.ModelSerializer):
    """Output serializer for authenticated landlord's own garage listings."""

    foto_principal = serializers.SerializerMethodField()

    class Meta:
        model = Cochera
        fields = [
            'id_cochera',
            'nombre',
            'direccion',
            'tarifa_hora',
            'espacios_totales',
            'tipo_vehiculo',
            'estado_cochera',
            'foto_principal',
        ]

    def get_foto_principal(self, obj: Cochera) -> str | None:
        return _get_primary_photo_url(obj)


class CocheraDetailSerializer(serializers.ModelSerializer):
    """Output serializer for full garage detail and updated representations."""

    arrendador = LandlordSummarySerializer(read_only=True)
    calificacion_promedio = serializers.SerializerMethodField()
    total_resenas = serializers.SerializerMethodField()
    fotos = FotoCocheraSerializer(many=True, read_only=True)
    disponibilidades = DisponibilidadSerializer(many=True, read_only=True)

    class Meta:
        model = Cochera
        fields = [
            'id_cochera',
            'arrendador',
            'nombre',
            'direccion',
            'latitud',
            'longitud',
            'metros_cuadrados',
            'espacios_totales',
            'tarifa_hora',
            'tipo_vehiculo',
            'estado_cochera',
            'calificacion_promedio',
            'total_resenas',
            'fotos',
            'disponibilidades',
        ]

    def get_calificacion_promedio(self, obj: Cochera):
        return get_garage_rating_summary(obj)['calificacion_promedio']

    def get_total_resenas(self, obj: Cochera) -> int:
        return get_garage_rating_summary(obj)['total_resenas']


class CocheraAdminValidationSerializer(serializers.Serializer):
    """Input serializer for administrative validation of garage publications (RN-10)."""

    estado_cochera = serializers.CharField()
    motivo = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class AdminGarageLandlordSerializer(serializers.ModelSerializer):
    """Landlord summary embedded in administrative garage review lists."""

    class Meta:
        model = Usuario
        fields = ['id_usuario', 'nombre_completo', 'correo', 'telefono']


class AdminGarageReviewListSerializer(serializers.ModelSerializer):
    """Output serializer for administrative garage review listing (`GET /admin/cocheras`)."""

    arrendador = AdminGarageLandlordSerializer(read_only=True)
    foto_principal = serializers.SerializerMethodField()
    fotos = FotoCocheraSerializer(many=True, read_only=True)
    disponibilidades = DisponibilidadSerializer(many=True, read_only=True)

    class Meta:
        model = Cochera
        fields = [
            'id_cochera',
            'nombre',
            'direccion',
            'latitud',
            'longitud',
            'metros_cuadrados',
            'espacios_totales',
            'tarifa_hora',
            'tipo_vehiculo',
            'estado_cochera',
            'foto_principal',
            'arrendador',
            'fotos',
            'disponibilidades',
        ]

    def get_foto_principal(self, obj: Cochera) -> str | None:
        return _get_primary_photo_url(obj)
