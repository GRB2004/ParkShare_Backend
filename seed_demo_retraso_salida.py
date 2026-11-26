"""
Script reutilizable para cargar en la base de datos real (`parkshare_db`) el escenario de demostración
de tolerancia de 10 minutos sin cargo (RN-02) y recargo de $10.00 MXN por minuto de retraso al recoger
el vehículo después de vencer la tolerancia (RN-03 / RN-06).

Uso:
    .\\venv\\Scripts\\python.exe seed_demo_retraso_salida.py
    .\\venv\\Scripts\\python.exe seed_demo_retraso_salida.py --minutos 20   # 10 min tolerancia + 10 min retraso = $100 MXN
    .\\venv\\Scripts\\python.exe seed_demo_retraso_salida.py --minutos 9    # A 1 min de agotar los 10 min sin cargo (RN-02)
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


def seed_late_exit_demo(minutes_past_end: int = 20) -> None:
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
    from ParkShare_Backend.services.reserva_service import (
        TOLERANCE_MINUTES,
        calculate_late_surcharge,
    )

    if minutes_past_end < 0:
        raise ValueError('El parámetro --minutos debe ser mayor o igual a 0.')

    now = timezone.now()
    end_dt = now - timedelta(minutes=minutes_past_end)
    start_dt = end_dt - timedelta(hours=2)
    tolerance_end_dt = end_dt + timedelta(minutes=TOLERANCE_MINUTES)

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
            correo='demo.salida@outlook.com',
            defaults={
                'nombre_completo': 'Sofía Demo Retraso Salida',
                'telefono': '5533445566',
                'fecha_nacimiento': date(1996, 4, 18),
                'rol': UserRole.ARRENDATARIO,
                'estado_cuenta': AccountStatus.ACTIVA,
                'placas': 'RET-100-Z',
                'modelo_vehiculo': 'Toyota Corolla 2024',
            },
        )
        tenant.rol = UserRole.ARRENDATARIO
        tenant.estado_cuenta = AccountStatus.ACTIVA
        tenant.placas = 'RET-100-Z'
        tenant.modelo_vehiculo = 'Toyota Corolla 2024'
        tenant.set_password('Demo12345!')
        tenant.save()

        # Cierra reservas activas previas de este arrendatario demo para re-ejecutar limpiamente (RN-08)
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

        base_amount = Decimal('80.00')
        reservation = Reserva.objects.create(
            arrendatario=tenant,
            cochera=garage,
            fecha_hora_inicio=start_dt,
            fecha_hora_fin=end_dt,
            monto_base=base_amount,
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.EN_USO,
        )
        Reserva.objects.filter(pk=reservation.id_reserva).update(
            fecha_creacion=start_dt - timedelta(minutes=30)
        )

        Pago.objects.create(
            reserva=reservation,
            monto_total=base_amount,
            metodo='TARJETA',
            referencia_pasarela=f'PS-DEMO-EXIT-{reservation.id_reserva}',
            estado=PaymentStatus.APROBADO,
        )

        # QR de entrada ya consumido al iniciar la estancia
        CodigoQR.objects.create(
            reserva=reservation,
            token_qr=generate_sha256_qr_token(),
            tipo_qr=QRCodeType.ENTRADA,
            usado=True,
            fecha_emision=start_dt - timedelta(minutes=15),
            expiracion=end_dt,
        )

        # QR de salida activo (+24h de vigencia para permitir cobro de recargo RN-03 / RN-06)
        qr_exit = CodigoQR.objects.create(
            reserva=reservation,
            token_qr=generate_sha256_qr_token(),
            tipo_qr=QRCodeType.SALIDA,
            usado=False,
            fecha_emision=start_dt,
            expiracion=now + timedelta(hours=24),
        )

    late_minutes, live_surcharge = calculate_late_surcharge(end_dt, now)
    estimated_total = (base_amount + live_surcharge).quantize(Decimal('0.01'))

    local_end = timezone.localtime(end_dt).strftime('%Y-%m-%d %H:%M:%S')
    local_tol = timezone.localtime(tolerance_end_dt).strftime('%Y-%m-%d %H:%M:%S')
    local_now = timezone.localtime(now).strftime('%Y-%m-%d %H:%M:%S')

    print('=' * 72)
    print('ESCENARIO DEMO RN-02 / RN-03 (SALIDA CON RETRASO) CARGADO EN parkshare_db')
    print('=' * 72)
    print(f'Reserva ID             : #{reservation.id_reserva} (estado: {reservation.estado})')
    print(f'Cochera                : {garage.nombre} (ID #{garage.id_cochera})')
    print(f'Fin reservado          : {local_end} (hace {minutes_past_end} min)')
    print(f'Fin tolerancia (RN-02) : {local_tol} (+{TOLERANCE_MINUTES} min sin cargo)')
    print(f'Hora actual            : {local_now}')
    print(f'Retraso cobrable actual: {late_minutes} min -> Recargo (RN-03): ${live_surcharge:.2f} MXN')
    print(f'Monto base + recargo   : ${base_amount:.2f} + ${live_surcharge:.2f} = ${estimated_total:.2f} MXN')
    print(f'Token QR SALIDA        : {qr_exit.token_qr}')
    print('-' * 72)
    print('CREDENCIALES PARA DEMOSTRACION EN LA APP MOVIL:')
    print('  Arrendatario         : demo.salida@outlook.com     | Password: Demo12345!')
    print('  Arrendador           : demo.arrendador@gmail.com   | Password: Demo12345!')
    print('-' * 72)
    print('Pasos para demostrar en Expo:')
    print('  1. Inicia sesion como Arrendatario (demo.salida@outlook.com) -> Mis reservas:')
    print('     Veras la reserva EN_USO con el contador de retraso y recargo de $10 MXN/min en vivo.')
    print('  2. Inicia sesion como Arrendador (demo.arrendador@gmail.com) -> Escanear QR:')
    print('     Toca el QR de SALIDA en "Pruebas rapidas" (o escanea el QR del arrendatario)')
    print('     para finalizar la estancia y ver el cobro exacto de la penalizacion.')
    print('=' * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            'Carga en parkshare_db una reserva EN_USO para demostrar la tolerancia de 10 min '
            '(RN-02) y el cobro de $10 MXN/min al superarla (RN-03).'
        )
    )
    parser.add_argument(
        '--minutos',
        type=int,
        default=20,
        help=(
            'Minutos transcurridos desde fecha_hora_fin (por defecto: 20 -> 10 min de tolerancia '
            'sin cargo + 10 min de retraso = $100.00 MXN; usa --minutos 9 para probar dentro de tolerancia).'
        ),
    )
    args = parser.parse_args()
    setup_django()
    seed_late_exit_demo(minutes_past_end=args.minutos)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Error al cargar datos de demostración: {exc}', file=sys.stderr)
        sys.exit(1)
