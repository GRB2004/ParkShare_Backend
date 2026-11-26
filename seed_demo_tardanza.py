"""
Script reutilizable para cargar en la base de datos real (`parkshare_db`) el escenario de demostración
de llegada tardía (> 20 minutos después de `fecha_hora_inicio`, Regla RN-07).

Uso:
    .\\venv\\Scripts\\python.exe seed_demo_tardanza.py
    .\\venv\\Scripts\\python.exe seed_demo_tardanza.py --minutos 25
"""

import argparse
from datetime import date, time, timedelta
from decimal import Decimal
import os
import sys

import django


def setup_django() -> None:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ParkShare_Backend.settings')
    django.setup()


def seed_late_arrival_demo(minutes_late: int = 25) -> None:
    from django.db import transaction
    from django.utils import timezone

    from ParkShare_Backend.models import (
        AccountStatus,
        Cochera,
        CodigoQR,
        Disponibilidad,
        FotoCochera,
        GarageStatus,
        Pago,
        PaymentStatus,
        QRCodeType,
        Reserva,
        ReservationStatus,
        UserRole,
        Usuario,
        VehicleType,
        WeekdayChoice,
    )
    from ParkShare_Backend.services.qr_service import generate_sha256_qr_token

    if minutes_late <= 20:
        print('Aviso: Para exceder la tolerancia de entrada (RN-07), --minutos debe ser mayor a 20.')

    now = timezone.now()
    start_dt = now - timedelta(minutes=minutes_late)
    end_dt = start_dt + timedelta(hours=2)

    with transaction.atomic():
        host, _ = Usuario.objects.get_or_create(
            correo='demo.arrendador@gmail.com',
            defaults={
                'nombre_completo': 'Carlos Demo Arrendador',
                'telefono': '5599887766',
                'fecha_nacimiento': date(1988, 5, 10),
                'rol': UserRole.ARRENDADOR,
                'estado_cuenta': AccountStatus.ACTIVA,
                'cuenta_cobro': '012180001234567890',
            },
        )
        host.rol = UserRole.ARRENDADOR
        host.estado_cuenta = AccountStatus.ACTIVA
        host.cuenta_cobro = host.cuenta_cobro or '012180001234567890'
        host.set_password('Demo12345!')
        host.save()

        tenant, _ = Usuario.objects.get_or_create(
            correo='demo.tarde@outlook.com',
            defaults={
                'nombre_completo': 'Luis Demo Tardanza',
                'telefono': '5511223344',
                'fecha_nacimiento': date(1997, 8, 15),
                'rol': UserRole.ARRENDATARIO,
                'estado_cuenta': AccountStatus.ACTIVA,
                'placas': 'TRD-202-X',
                'modelo_vehiculo': 'Mazda Tres 2023',
            },
        )
        tenant.rol = UserRole.ARRENDATARIO
        tenant.estado_cuenta = AccountStatus.ACTIVA
        tenant.placas = 'TRD-202-X'
        tenant.modelo_vehiculo = 'Mazda Tres 2023'
        tenant.set_password('Demo12345!')
        tenant.save()

        # Cierra cualquier reserva activa previa de este usuario demo para poder re-ejecutar el script las veces necesarias (RN-08)
        prev_active_ids = list(
            Reserva.objects.filter(
                arrendatario=tenant,
                estado__in=[
                    ReservationStatus.SOLICITADA,
                    ReservationStatus.CONFIRMADA,
                    ReservationStatus.EN_USO,
                ],
            ).values_list('id_reserva', flat=True)
        )
        if prev_active_ids:
            CodigoQR.objects.filter(reserva_id__in=prev_active_ids, usado=False).update(usado=True)
            Reserva.objects.filter(id_reserva__in=prev_active_ids).update(
                estado=ReservationStatus.CANCELADA
            )

        garage, _ = Cochera.objects.get_or_create(
            arrendador=host,
            nombre='Cochera Demo Tolerancia Zócalo',
            defaults={
                'direccion': 'Av. Reforma 100, Zócalo Centro, Puebla',
                'latitud': Decimal('19.04360000'),
                'longitud': Decimal('-98.19810000'),
                'metros_cuadrados': Decimal('35.00'),
                'espacios_totales': 4,
                'tarifa_hora': Decimal('40.00'),
                'tipo_vehiculo': VehicleType.AMBOS,
                'estado_cochera': GarageStatus.APROBADA,
            },
        )
        if garage.estado_cochera != GarageStatus.APROBADA:
            garage.estado_cochera = GarageStatus.APROBADA
            garage.save(update_fields=['estado_cochera'])

        FotoCochera.objects.get_or_create(
            cochera=garage,
            url_foto='https://images.unsplash.com/photo-1590674899484-d5640e854abe?w=800',
        )
        for day_code in WeekdayChoice.values:
            Disponibilidad.objects.get_or_create(
                cochera=garage,
                dia_semana=day_code,
                defaults={
                    'hora_inicio': time(0, 0),
                    'hora_fin': time(23, 59, 59),
                },
            )

        reservation = Reserva.objects.create(
            arrendatario=tenant,
            cochera=garage,
            fecha_hora_inicio=start_dt,
            fecha_hora_fin=end_dt,
            monto_base=Decimal('80.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.CONFIRMADA,
        )
        Reserva.objects.filter(pk=reservation.id_reserva).update(
            fecha_creacion=start_dt - timedelta(minutes=30)
        )

        Pago.objects.create(
            reserva=reservation,
            monto_total=Decimal('80.00'),
            metodo='TARJETA',
            referencia_pasarela=f'PS-DEMO-LATE-{reservation.id_reserva}',
            estado=PaymentStatus.APROBADO,
        )

        qr_entry = CodigoQR.objects.create(
            reserva=reservation,
            token_qr=generate_sha256_qr_token(),
            tipo_qr=QRCodeType.ENTRADA,
            usado=False,
            fecha_emision=start_dt - timedelta(minutes=15),
            expiracion=end_dt,
        )

    local_start = timezone.localtime(start_dt).strftime('%Y-%m-%d %H:%M:%S')
    local_now = timezone.localtime(now).strftime('%Y-%m-%d %H:%M:%S')
    print('=' * 68)
    print('ESCENARIO DEMO RN-07 CARGADO EXITOSAMENTE EN parkshare_db')
    print('=' * 68)
    print(f'Reserva ID          : #{reservation.id_reserva} (estado: {reservation.estado})')
    print(f'Cochera             : {garage.nombre} (ID #{garage.id_cochera})')
    print(f'Inicio programado   : {local_start} (hace {minutes_late} minutos)')
    print(f'Hora actual         : {local_now} (> 20 min de tolerancia)')
    print(f'Token QR ENTRADA    : {qr_entry.token_qr}')
    print('-' * 68)
    print('CREDENCIALES PARA DEMOSTRACION EN LA APP MOVIL:')
    print('  Arrendatario      : demo.tarde@outlook.com      | Password: Demo12345!')
    print('  Arrendador        : demo.arrendador@gmail.com   | Password: Demo12345!')
    print('-' * 68)
    print('Pasos en Expo: Inicia sesion como Arrendador -> Escanear QR -> Simular escaneo QR')
    print('(o escanea el QR mostrado en la cuenta del Arrendatario).')
    print('=' * 68)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Carga el caso de prueba de llegada tardía (> 20 min) en parkshare_db.'
    )
    parser.add_argument(
        '--minutos',
        type=int,
        default=25,
        help='Minutos transcurridos desde fecha_hora_inicio (por defecto: 25).',
    )
    args = parser.parse_args()
    setup_django()
    seed_late_arrival_demo(minutes_late=args.minutos)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Error al cargar datos de demostración: {exc}', file=sys.stderr)
        sys.exit(1)
