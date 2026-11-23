from django.contrib import admin

from .models import (
    Cochera,
    CodigoQR,
    Disponibilidad,
    DocumentoArrendador,
    FotoCochera,
    Reserva,
    SuspensionCuenta,
    Usuario,
)


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('id_usuario', 'nombre_completo', 'correo', 'rol', 'estado_cuenta')
    list_filter = ('rol', 'estado_cuenta')
    search_fields = ('nombre_completo', 'correo')
    readonly_fields = ('password', 'last_login', 'token_version')


@admin.register(DocumentoArrendador)
class DocumentoArrendadorAdmin(admin.ModelAdmin):
    list_display = ('id_documento', 'arrendador', 'estado_verificacion')
    list_filter = ('estado_verificacion',)


@admin.register(SuspensionCuenta)
class SuspensionCuentaAdmin(admin.ModelAdmin):
    list_display = ('id_suspension', 'usuario', 'admin', 'estado', 'fecha_suspension')
    list_filter = ('estado',)


@admin.register(Cochera)
class CocheraAdmin(admin.ModelAdmin):
    list_display = ('id_cochera', 'nombre', 'arrendador', 'tarifa_hora', 'espacios_totales', 'tipo_vehiculo', 'estado_cochera')
    list_filter = ('estado_cochera', 'tipo_vehiculo')
    search_fields = ('nombre', 'direccion', 'arrendador__nombre_completo')


@admin.register(FotoCochera)
class FotoCocheraAdmin(admin.ModelAdmin):
    list_display = ('id_foto', 'cochera', 'url_foto')


@admin.register(Disponibilidad)
class DisponibilidadAdmin(admin.ModelAdmin):
    list_display = ('id_disponibilidad', 'cochera', 'dia_semana', 'hora_inicio', 'hora_fin')
    list_filter = ('dia_semana',)


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ('id_reserva', 'cochera', 'arrendatario', 'fecha_hora_inicio', 'fecha_hora_fin', 'estado', 'monto_base', 'monto_penalizacion')
    list_filter = ('estado',)
    search_fields = ('cochera__nombre', 'arrendatario__nombre_completo', 'arrendatario__correo')


@admin.register(CodigoQR)
class CodigoQRAdmin(admin.ModelAdmin):
    list_display = ('id_qr', 'reserva', 'tipo_qr', 'usado', 'fecha_emision', 'expiracion')
    list_filter = ('tipo_qr', 'usado')
    search_fields = ('token_qr', 'reserva__id_reserva')
