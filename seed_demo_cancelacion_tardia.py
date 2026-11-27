"""
Script reutilizable para cargar en la base de datos real (`parkshare_db`) el escenario de demostración
de cancelación tardía con multa de $100.00 MXN después de los primeros 10 minutos desde la creación
de una reserva CONFIRMADA (RN-05, y opcionalmente RN-04 con --minutos <= 10).

Uso:
    .\\venv\\Scripts\\python.exe seed_demo_cancelacion_tardia.py
    .\\venv\\Scripts\\python.exe seed_demo_cancelacion_tardia.py --minutos 15   # > 10 min -> Multa $100.00 MXN (RN-05)
    .\\venv\\Scripts\\python.exe seed_demo_cancelacion_tardia.py --minutos 9    # <= 10 min -> Sin costo $0.00 MXN (RN-04)
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


def seed_late_cancellation_demo(minutes_since_creation: int = 15) -> None:
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
        calculate_cancellation_penalty,
    )

    if minutes_since_creation < 0:
        raise ValueError('El parámetro --minutos debe ser mayor o igual a 0.')

    now = timezone.now()
    created_dt = now - timedelta(minutes=minutes_since_creation)
    start_dt = now + timedelta(hours=1)
    end_dt = start_dt + timedelta(hours=3)
    free_cancel_limit_dt = created_dt + timedelta(minutes=TOLERANCE_MINUTES)

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
            correo='demo.cancelar@outlook.com',
            defaults={
                'nombre_completo': 'Mateo Demo Cancelación',
                'telefono': '5544556677',
                'fecha_nacimiento': date(1995, 2, 12),
                'rol': UserRole.ARRENDATARIO,
                'estado_cuenta': AccountStatus.ACTIVA,
                'placas': 'CAN-100-M',
                'modelo_vehiculo': 'Honda Civic 2023',
            },
        )
        tenant.rol = UserRole.ARRENDATARIO
        tenant.estado_cuenta = AccountStatus.ACTIVA
        tenant.placas = 'CAN-100-M'
        tenant.modelo_vehiculo = 'Honda Civic 2023'
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

        base_amount = Decimal('120.00')
        reservation = Reserva.objects.create(
            arrendatario=tenant,
            cochera=garage,
            fecha_hora_inicio=start_dt,
            fecha_hora_fin=end_dt,
            monto_base=base_amount,
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.CONFIRMADA,
        )
        Reserva.objects.filter(pk=reservation.id_reserva).update(fecha_creacion=created_dt)
        reservation.refresh_from_db()

        Pago.objects.create(
            reserva=reservation,
            monto_total=base_amount,
            metodo='TARJETA',
            referencia_pasarela=f'PS-DEMO-CANCEL-{reservation.id_reserva}',
            estado=PaymentStatus.APROBADO,
        )

        CodigoQR.objects.create(
            reserva=reservation,
            token_qr=generate_sha256_qr_token(),
            tipo_qr=QRCodeType.ENTRADA,
            usado=False,
            fecha_emision=created_dt,
            expiracion=end_dt,
        )

    applies_penalty, expected_penalty = calculate_cancellation_penalty(
        reservation.estado,
        reservation.fecha_creacion,
        now,
        start_dt=reservation.fecha_hora_inicio,
        end_dt=reservation.fecha_hora_fin,
    )

    local_created = timezone.localtime(created_dt).strftime('%Y-%m-%d %H:%M:%S')
    local_free_limit = timezone.localtime(free_cancel_limit_dt).strftime('%Y-%m-%d %H:%M:%S')
    local_now = timezone.localtime(now).strftime('%Y-%m-%d %H:%M:%S')
    local_start = timezone.localtime(start_dt).strftime('%Y-%m-%d %H:%M:%S')
    rule_label = 'RN-05 (Multa por cancelación tardía)' if applies_penalty else 'RN-04 (Cancelación sin costo)'

    print('=' * 74)
    print('ESCENARIO DEMO RN-05 (CANCELACION TARDIA) CARGADO EN parkshare_db')
    print('=' * 74)
    print(f'Reserva ID                : #{reservation.id_reserva} (estado: {reservation.estado})')
    print(f'Cochera                   : {garage.nombre} (ID #{garage.id_cochera})')
    print(f'Fecha creacion            : {local_created} (hace {minutes_since_creation} min)')
    print(f'Limite sin costo (RN-04)  : {local_free_limit} (+{TOLERANCE_MINUTES} min)')
    print(f'Hora actual               : {local_now}')
    print(f'Inicio programado         : {local_start}')
    print(f'Monto base pagado         : ${base_amount:.2f} MXN')
    print(f'Regla al cancelar ahora   : {rule_label} -> Penalizacion: ${expected_penalty:.2f} MXN')
    print('-' * 74)
    print('CREDENCIALES PARA DEMOSTRACION EN LA APP MOVIL:')
    print('  Arrendatario            : demo.cancelar@outlook.com   | Password: Demo12345!')
    print('  Arrendador              : demo.arrendador@gmail.com   | Password: Demo12345!')
    print('-' * 74)
    print('Pasos para demostrar en Expo:')
    print('  1. Inicia sesion como Arrendatario (demo.cancelar@outlook.com) -> Mis reservas.')
    print('  2. Abre la reserva CONFIRMADA y toca "Cancelar reservacion":')
    print('     Veras el aviso de RN-05 ($100.00 MXN) y al confirmar pasara a CANCELADA')
    print('     mostrando la multa aplicada de $100.00 MXN en el historial y en cobros del arrendador.')
    print('=' * 74)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            'Carga en parkshare_db una reserva CONFIRMADA creada hace >10 min para demostrar '
            'la multa de $100.00 MXN por cancelación tardía (RN-05).'
        )
    )
    parser.add_argument(
        '--minutos',
        type=int,
        default=15,
        help=(
            'Minutos transcurridos desde fecha_creacion (por defecto: 15 -> aplica multa de '
            '$100.00 MXN por RN-05; usa --minutos 9 para probar cancelación gratuita RN-04).'
        ),
    )
    args = parser.parse_args()
    setup_django()
    seed_late_cancellation_demo(minutes_since_creation=args.minutos)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Error al cargar datos de demostración: {exc}', file=sys.stderr)
        sys.exit(1)
