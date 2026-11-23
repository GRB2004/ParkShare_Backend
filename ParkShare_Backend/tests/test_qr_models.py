from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    CodigoQR,
    GarageStatus,
    QRCodeType,
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
        nombre_completo='Usuario Prueba QR',
        correo=email,
        telefono='5512345678',
        password='MiPasswordSegura123!',
        fecha_nacimiento=date(1992, 6, 15),
        rol=role,
        estado_cuenta=AccountStatus.ACTIVA,
        **extra,
    )


class CodigoQRModelTests(TestCase):
    def setUp(self):
        self.landlord = _create_user('arrendador.qr@gmail.com', UserRole.ARRENDADOR)
        self.tenant = _create_user('arrendatario.qr@outlook.com', UserRole.ARRENDATARIO)
        self.garage = Cochera.objects.create(
            arrendador=self.landlord,
            nombre='Cochera QR',
            direccion='Av. Insurgentes Sur 1450',
            latitud=Decimal('19.37124500'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('35.50'),
            espacios_totales=2,
            tarifa_hora=Decimal('45.00'),
            tipo_vehiculo=VehicleType.AMBOS,
            estado_cochera=GarageStatus.APROBADA,
        )
        self.issued_at = datetime(2026, 11, 25, 14, 0, tzinfo=MEXICO_TZ)
        self.start_dt = datetime(2026, 11, 25, 16, 0, tzinfo=MEXICO_TZ)
        self.end_dt = datetime(2026, 11, 25, 18, 0, tzinfo=MEXICO_TZ)
        self.reservation = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.end_dt,
            monto_base=Decimal('90.00'),
            estado=ReservationStatus.CONFIRMADA,
        )
        self.reservation2 = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt + timedelta(hours=3),
            fecha_hora_fin=self.end_dt + timedelta(hours=3),
            monto_base=Decimal('90.00'),
            estado=ReservationStatus.CONFIRMADA,
        )
        self.token_a = 'a' * 64
        self.token_b = 'b' * 64

    def test_create_codigo_qr_defaults_and_table_metadata(self):
        self.assertEqual(CodigoQR._meta.db_table, 'codigo_qr')
        constraint_names = {c.name for c in CodigoQR._meta.constraints}
        self.assertIn('uq_codigo_qr_reserva_tipo', constraint_names)
        index_names = {idx.name for idx in CodigoQR._meta.indexes}
        self.assertIn('idx_qr_reserva_tipo_usado', index_names)

        qr = CodigoQR(
            reserva=self.reservation,
            token_qr=self.token_a,
            tipo_qr=QRCodeType.ENTRADA,
            fecha_emision=self.issued_at,
            expiracion=self.end_dt,
        )
        qr.full_clean()
        qr.save()

        self.assertIsNotNone(qr.id_qr)
        self.assertFalse(qr.usado)
        self.assertEqual(qr.tipo_qr, QRCodeType.ENTRADA)

    def test_codigo_qr_validates_tipo_token_format_and_expiration(self):
        bad_type = CodigoQR(
            reserva=self.reservation,
            token_qr=self.token_a,
            tipo_qr='INVALIDO',
            fecha_emision=self.issued_at,
            expiracion=self.end_dt,
        )
        with self.assertRaises(ValidationError) as ctx_type:
            bad_type.full_clean()
        self.assertIn('tipo_qr', ctx_type.exception.message_dict)

        for bad_token in ('abc', 'z' * 64, 'a' * 63):
            bad_qr = CodigoQR(
                reserva=self.reservation,
                token_qr=bad_token,
                tipo_qr=QRCodeType.ENTRADA,
                fecha_emision=self.issued_at,
                expiracion=self.end_dt,
            )
            with self.assertRaises(ValidationError) as ctx_tok:
                bad_qr.full_clean()
            self.assertIn('token_qr', ctx_tok.exception.message_dict)

        bad_exp = CodigoQR(
            reserva=self.reservation,
            token_qr=self.token_a,
            tipo_qr=QRCodeType.ENTRADA,
            fecha_emision=self.end_dt,
            expiracion=self.end_dt,
        )
        with self.assertRaises(ValidationError) as ctx_exp:
            bad_exp.full_clean()
        self.assertIn('expiracion', ctx_exp.exception.message_dict)

    def test_codigo_qr_unique_constraints_enforce_integrity_error(self):
        CodigoQR.objects.create(
            reserva=self.reservation,
            token_qr=self.token_a,
            tipo_qr=QRCodeType.ENTRADA,
            fecha_emision=self.issued_at,
            expiracion=self.end_dt,
        )

        # Duplicate token_qr on another reservation fails UNIQUE(token_qr)
        with self.assertRaises(IntegrityError):
            CodigoQR.objects.create(
                reserva=self.reservation2,
                token_qr=self.token_a,
                tipo_qr=QRCodeType.ENTRADA,
                fecha_emision=self.issued_at,
                expiracion=self.end_dt,
            )

    def test_codigo_qr_unique_reserva_and_tipo_qr(self):
        CodigoQR.objects.create(
            reserva=self.reservation,
            token_qr=self.token_a,
            tipo_qr=QRCodeType.ENTRADA,
            fecha_emision=self.issued_at,
            expiracion=self.end_dt,
        )
        with self.assertRaises(IntegrityError):
            CodigoQR.objects.create(
                reserva=self.reservation,
                token_qr=self.token_b,
                tipo_qr=QRCodeType.ENTRADA,
                fecha_emision=self.issued_at,
                expiracion=self.end_dt,
            )
