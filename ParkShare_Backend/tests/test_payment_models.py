from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    Disponibilidad,
    GarageStatus,
    Pago,
    PaymentStatus,
    Reserva,
    ReservationStatus,
    RetiroCobro,
    UserRole,
    Usuario,
    VehicleType,
    WeekdayChoice,
    WithdrawalStatus,
)

MEXICO_TZ = ZoneInfo('America/Mexico_City')


def _create_user(email: str, role: str, status: str = AccountStatus.ACTIVA) -> Usuario:
    extra = {'cuenta_cobro': '012180001234567890'} if role == UserRole.ARRENDADOR else {
        'placas': 'ABC-123-D',
        'modelo_vehiculo': 'Volkswagen Jetta 2020',
    }
    return Usuario.objects.create_user(
        nombre_completo='Usuario Pagos ParkShare',
        correo=email,
        telefono='5512345678',
        password='MiPasswordSegura123!',
        fecha_nacimiento=date(1990, 5, 10),
        rol=role,
        estado_cuenta=status,
        **extra,
    )


def _create_garage(landlord: Usuario, rate: str = '40.00') -> Cochera:
    garage = Cochera.objects.create(
        arrendador=landlord,
        nombre='Cochera Pagos Centro',
        direccion='Av. Reforma 500',
        latitud=Decimal('19.42000000'),
        longitud=Decimal('-99.16000000'),
        metros_cuadrados=Decimal('32.00'),
        espacios_totales=2,
        tarifa_hora=Decimal(rate),
        tipo_vehiculo=VehicleType.AMBOS,
        estado_cochera=GarageStatus.APROBADA,
    )
    for day in WeekdayChoice.values:
        Disponibilidad.objects.create(
            cochera=garage,
            dia_semana=day,
            hora_inicio=time(0, 0),
            hora_fin=time(23, 59, 59),
        )
    return garage


class PaymentModelsTests(TestCase):
    def setUp(self):
        self.landlord = _create_user('host.pay@gmail.com', UserRole.ARRENDADOR)
        self.tenant = _create_user('tenant.pay@outlook.com', UserRole.ARRENDATARIO)
        self.garage = _create_garage(self.landlord)
        self.start_dt = datetime(2026, 11, 26, 10, 0, tzinfo=MEXICO_TZ)
        self.end_dt = datetime(2026, 11, 26, 12, 0, tzinfo=MEXICO_TZ)
        self.reservation = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.end_dt,
            monto_base=Decimal('80.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.SOLICITADA,
        )

    def test_create_pago_defaults_and_db_table(self):
        self.assertEqual(Pago._meta.db_table, 'pago')
        payment = Pago.objects.create(
            reserva=self.reservation,
            monto_total=Decimal('80.00'),
            metodo='TARJETA',
        )
        payment.full_clean()
        self.assertIsNotNone(payment.id_pago)
        self.assertEqual(payment.estado, PaymentStatus.PENDIENTE)
        self.assertIsNone(payment.referencia_pasarela)
        self.assertIsNotNone(payment.fecha_pago)
        self.assertEqual(payment.monto_total, Decimal('80.00'))

    def test_pago_rejects_invalid_estado_and_negative_amount(self):
        bad_state = Pago(
            reserva=self.reservation,
            monto_total=Decimal('80.00'),
            metodo='TARJETA',
            estado='INVALIDO',
        )
        with self.assertRaises(ValidationError):
            bad_state.full_clean()

        negative_payment = Pago(
            reserva=self.reservation,
            monto_total=Decimal('-0.01'),
            metodo='TARJETA',
            estado=PaymentStatus.PENDIENTE,
        )
        with self.assertRaises(ValidationError):
            negative_payment.full_clean()

    def test_pago_rejects_second_aprobado_for_same_reservation(self):
        Pago.objects.create(
            reserva=self.reservation,
            monto_total=Decimal('80.00'),
            metodo='TARJETA',
            referencia_pasarela='PS-PAY-0001',
            estado=PaymentStatus.APROBADO,
        )
        duplicate_approved = Pago(
            reserva=self.reservation,
            monto_total=Decimal('80.00'),
            metodo='TARJETA',
            referencia_pasarela='PS-PAY-0002',
            estado=PaymentStatus.APROBADO,
        )
        with self.assertRaises(ValidationError):
            duplicate_approved.full_clean()

    def test_create_retiro_cobro_and_constraints(self):
        self.assertEqual(RetiroCobro._meta.db_table, 'retiro_cobro')
        withdrawal = RetiroCobro.objects.create(
            arrendador=self.landlord,
            cuenta_cobro='012180001234567890',
            monto=Decimal('420.00'),
            referencia='PS-RET-0001',
        )
        withdrawal.full_clean()
        self.assertIsNotNone(withdrawal.id_retiro)
        self.assertEqual(withdrawal.estado, WithdrawalStatus.COMPLETADO)
        self.assertIsNotNone(withdrawal.fecha_retiro)

        # Duplicate reference rejected
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RetiroCobro.objects.create(
                    arrendador=self.landlord,
                    cuenta_cobro='012180001234567890',
                    monto=Decimal('100.00'),
                    referencia='PS-RET-0001',
                )

        # Non-positive amount rejected
        for bad_amount in (Decimal('0.00'), Decimal('-10.00')):
            bad_withdrawal = RetiroCobro(
                arrendador=self.landlord,
                cuenta_cobro='012180001234567890',
                monto=bad_amount,
                referencia=f'PS-RET-BAD-{bad_amount}',
            )
            with self.assertRaises(ValidationError):
                bad_withdrawal.full_clean()
