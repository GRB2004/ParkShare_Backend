from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db.models import RestrictedError
from django.test import TestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    GarageStatus,
    Reserva,
    ReservationStatus,
    UserRole,
    Usuario,
    VehicleType,
)

MEXICO_TZ = ZoneInfo('America/Mexico_City')


def _create_user(email: str, role: str) -> Usuario:
    extra = {'cuenta_cobro': '012180001234567890'} if role == UserRole.ARRENDADOR else {
        'placas': 'ABC-123-D',
        'modelo_vehiculo': 'Nissan Versa 2022',
    }
    return Usuario.objects.create_user(
        nombre_completo='Usuario Prueba Reserva',
        correo=email,
        telefono='5512345678',
        password='MiPasswordSegura123!',
        fecha_nacimiento=date(1992, 6, 15),
        rol=role,
        estado_cuenta=AccountStatus.ACTIVA,
        **extra,
    )


def _create_garage(landlord: Usuario) -> Cochera:
    return Cochera.objects.create(
        arrendador=landlord,
        nombre='Cochera Los Pinos',
        direccion='Av. Insurgentes Sur 1450',
        latitud=Decimal('19.37124500'),
        longitud=Decimal('-99.17834200'),
        metros_cuadrados=Decimal('35.50'),
        espacios_totales=2,
        tarifa_hora=Decimal('45.00'),
        tipo_vehiculo=VehicleType.AMBOS,
        estado_cochera=GarageStatus.APROBADA,
    )


class ReservaModelTests(TestCase):
    def setUp(self):
        self.landlord = _create_user('arrendador.reserva@gmail.com', UserRole.ARRENDADOR)
        self.tenant = _create_user('arrendatario.reserva@outlook.com', UserRole.ARRENDATARIO)
        self.garage = _create_garage(self.landlord)
        self.start_dt = datetime(2026, 11, 25, 10, 0, tzinfo=MEXICO_TZ)
        self.end_dt = self.start_dt + timedelta(minutes=90)

    def test_create_reserva_defaults_metadata_and_monto_total(self):
        self.assertEqual(Reserva._meta.db_table, 'reserva')
        self.assertEqual(
            set(ReservationStatus.values),
            {'SOLICITADA', 'CONFIRMADA', 'EN_USO', 'FINALIZADA', 'CANCELADA', 'EXPIRADA'},
        )
        index_names = {idx.name for idx in Reserva._meta.indexes}
        self.assertIn('idx_reserva_cupo', index_names)
        self.assertIn('idx_reserva_arrendatario', index_names)

        reservation = Reserva(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.end_dt,
            monto_base=Decimal('67.50'),
        )
        reservation.full_clean()
        reservation.save()

        self.assertIsNotNone(reservation.id_reserva)
        self.assertEqual(reservation.estado, ReservationStatus.SOLICITADA)
        self.assertEqual(reservation.monto_penalizacion, Decimal('0.00'))
        self.assertIsNone(reservation.hora_salida_real)
        self.assertIsNotNone(reservation.fecha_creacion)
        self.assertEqual(reservation.monto_total, Decimal('67.50'))

        reservation.monto_penalizacion = Decimal('100.00')
        self.assertEqual(reservation.monto_total, Decimal('167.50'))

    def test_reserva_validates_time_window_and_monetary_constraints(self):
        equal_times = Reserva(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.start_dt,
            monto_base=Decimal('45.00'),
        )
        with self.assertRaises(ValidationError) as ctx_eq:
            equal_times.full_clean()
        self.assertIn('fecha_hora_fin', ctx_eq.exception.message_dict)

        reversed_times = Reserva(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.end_dt,
            fecha_hora_fin=self.start_dt,
            monto_base=Decimal('45.00'),
        )
        with self.assertRaises(ValidationError) as ctx_rev:
            reversed_times.full_clean()
        self.assertIn('fecha_hora_fin', ctx_rev.exception.message_dict)

        zero_base = Reserva(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.end_dt,
            monto_base=Decimal('0.00'),
        )
        with self.assertRaises(ValidationError) as ctx_base:
            zero_base.full_clean()
        self.assertIn('monto_base', ctx_base.exception.message_dict)

        negative_penalty = Reserva(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.end_dt,
            monto_base=Decimal('45.00'),
            monto_penalizacion=Decimal('-1.00'),
        )
        with self.assertRaises(ValidationError) as ctx_pen:
            negative_penalty.full_clean()
        self.assertIn('monto_penalizacion', ctx_pen.exception.message_dict)

        bad_status = Reserva(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.end_dt,
            monto_base=Decimal('45.00'),
            estado='DESCONOCIDO',
        )
        with self.assertRaises(ValidationError) as ctx_status:
            bad_status.full_clean()
        self.assertIn('estado', ctx_status.exception.message_dict)

    def test_reserva_foreign_keys_enforce_on_delete_restrict(self):
        Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.end_dt,
            monto_base=Decimal('67.50'),
        )
        with self.assertRaises(RestrictedError):
            self.tenant.delete()
        with self.assertRaises(RestrictedError):
            self.garage.delete()

