import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    CodigoQR,
    Disponibilidad,
    GarageStatus,
    QRCodeType,
    Reserva,
    ReservationStatus,
    UserRole,
    Usuario,
    VehicleType,
    WeekdayChoice,
)
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    GoneError,
    NotFoundError,
    PermissionDeniedError,
)
from ParkShare_Backend.services.qr_service import (
    calculate_late_checkout_penalty,
    generate_sha256_qr_token,
    get_active_qr_for_reservation,
    issue_entry_qr,
    validate_scanned_qr,
)
from ParkShare_Backend.services.reserva_service import confirm_reservation, create_reservation

MEXICO_TZ = ZoneInfo('America/Mexico_City')
SHA256_LOWER_REGEX = re.compile(r'^[0-9a-f]{64}$')


def _create_user(email: str, role: str, status: str = AccountStatus.ACTIVA) -> Usuario:
    extra = {'cuenta_cobro': '012180001234567890'} if role == UserRole.ARRENDADOR else {
        'placas': 'ABC-123-D',
        'modelo_vehiculo': 'Volkswagen Jetta 2020',
    }
    return Usuario.objects.create_user(
        nombre_completo='Carlos Mendoza Ruiz',
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
        nombre='Cochera Del Valle',
        direccion='Calle Providencia 200',
        latitud=Decimal('19.38000000'),
        longitud=Decimal('-99.17000000'),
        metros_cuadrados=Decimal('30.00'),
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


class QRServicesTests(TestCase):
    def setUp(self):
        self.landlord = _create_user('host.qr@gmail.com', UserRole.ARRENDADOR)
        self.other_landlord = _create_user('other.host@gmail.com', UserRole.ARRENDADOR)
        self.pending_landlord = _create_user(
            'pending.host@gmail.com', UserRole.ARRENDADOR, status=AccountStatus.PENDIENTE
        )
        self.tenant = _create_user('tenant.qr@outlook.com', UserRole.ARRENDATARIO)
        self.other_tenant = _create_user('tenant2.qr@outlook.com', UserRole.ARRENDATARIO)
        self.garage = _create_garage(self.landlord, rate='40.00')

        self.now = datetime(2026, 11, 25, 14, 0, tzinfo=MEXICO_TZ)
        self.start_dt = datetime(2026, 11, 25, 16, 0, tzinfo=MEXICO_TZ)
        self.end_dt = datetime(2026, 11, 25, 18, 0, tzinfo=MEXICO_TZ)

    def test_generate_sha256_qr_token_format_and_uniqueness(self):
        t1 = generate_sha256_qr_token()
        t2 = generate_sha256_qr_token()
        self.assertRegex(t1, SHA256_LOWER_REGEX)
        self.assertRegex(t2, SHA256_LOWER_REGEX)
        self.assertNotEqual(t1, t2)

    def test_calculate_late_checkout_penalty_boundaries(self):
        end_dt = self.end_dt
        self.assertEqual(
            calculate_late_checkout_penalty(end_dt, end_dt - timedelta(minutes=5)),
            (0, Decimal('0.00')),
        )
        self.assertEqual(
            calculate_late_checkout_penalty(end_dt, end_dt + timedelta(minutes=10)),
            (0, Decimal('0.00')),
        )
        self.assertEqual(
            calculate_late_checkout_penalty(end_dt, end_dt + timedelta(minutes=10, seconds=1)),
            (1, Decimal('10.00')),
        )
        self.assertEqual(
            calculate_late_checkout_penalty(end_dt, end_dt + timedelta(minutes=20)),
            (10, Decimal('100.00')),
        )

    def test_issue_entry_qr_auto_emit_idempotency_and_invalid_states(self):
        reservation = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=self.now
        )

        # SOLICITADA cannot issue entry QR
        with self.assertRaises(ConflictError):
            issue_entry_qr(reservation, now=self.now)

        # Confirming automatically emits ENTRADA QR
        confirmed = confirm_reservation(reservation.id_reserva, by_system=True, now=self.now)
        self.assertEqual(confirmed.estado, ReservationStatus.CONFIRMADA)
        qr_entry = CodigoQR.objects.get(reserva=confirmed, tipo_qr=QRCodeType.ENTRADA)
        self.assertRegex(qr_entry.token_qr, SHA256_LOWER_REGEX)
        self.assertFalse(qr_entry.usado)
        self.assertEqual(qr_entry.expiracion, self.end_dt)

        # Idempotent second call returns exact same QR
        qr_again = issue_entry_qr(confirmed, now=self.now)
        self.assertEqual(qr_again.id_qr, qr_entry.id_qr)
        self.assertEqual(CodigoQR.objects.filter(reserva=confirmed, tipo_qr=QRCodeType.ENTRADA).count(), 1)

    def test_validate_entry_and_exit_qr_full_lifecycle_rn02_rn03_rn06(self):
        reservation = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=self.now
        )
        confirm_reservation(reservation.id_reserva, by_system=True, now=self.now)

        # Too early (> 15 min before start) -> ConflictError without consuming QR
        entry_qr = get_active_qr_for_reservation(reservation.id_reserva, self.tenant)
        with self.assertRaises(ConflictError):
            validate_scanned_qr(entry_qr.token_qr, self.landlord, now=self.start_dt - timedelta(minutes=30))
        entry_qr.refresh_from_db()
        self.assertFalse(entry_qr.usado)

        # Within 15-min grace arrival window (10 min early) -> 200 entry
        entry_result = validate_scanned_qr(
            entry_qr.token_qr, self.landlord, now=self.start_dt - timedelta(minutes=10)
        )
        self.assertEqual(entry_result['tipo_qr'], QRCodeType.ENTRADA)
        self.assertEqual(entry_result['estado_reserva'], ReservationStatus.EN_USO)
        self.assertTrue(entry_result['qr_salida_emitido'])
        self.assertNotIn('token_qr', entry_result)
        self.assertEqual(entry_result['verificacion']['arrendatario']['placas'], 'ABC-123-D')

        entry_qr.refresh_from_db()
        self.assertTrue(entry_qr.usado)

        # Reusing already scanned entry QR -> ConflictError
        with self.assertRaises(ConflictError):
            validate_scanned_qr(entry_qr.token_qr, self.landlord, now=self.start_dt)

        # Tenant now retrieves active SALIDA QR
        exit_qr = get_active_qr_for_reservation(reservation.id_reserva, self.tenant)
        self.assertEqual(exit_qr.tipo_qr, QRCodeType.SALIDA)
        self.assertFalse(exit_qr.usado)
        self.assertGreater(exit_qr.expiracion, self.end_dt)

        # Late exit +20 min -> 10 min delay = $100.00 MXN + invokes payment adjustment hook
        with patch(
            'ParkShare_Backend.services.qr_service.apply_late_checkout_payment_adjustment_hook'
        ) as mock_hook:
            exit_result = validate_scanned_qr(
                exit_qr.token_qr.upper(),  # Uppercase normalization check
                self.landlord,
                now=self.end_dt + timedelta(minutes=20),
            )
            mock_hook.assert_called_once()

        self.assertEqual(exit_result['tipo_qr'], QRCodeType.SALIDA)
        self.assertEqual(exit_result['estado_reserva'], ReservationStatus.FINALIZADA)
        self.assertEqual(exit_result['verificacion']['tolerancia_minutos'], 10)
        self.assertEqual(exit_result['verificacion']['minutos_retraso'], 10)
        self.assertEqual(exit_result['verificacion']['monto_base'], '80.00')
        self.assertEqual(exit_result['verificacion']['monto_penalizacion'], '100.00')
        self.assertEqual(exit_result['verificacion']['monto_total'], '180.00')

        exit_qr.refresh_from_db()
        self.assertTrue(exit_qr.usado)

    def test_very_late_exit_qr_still_valid_3_hours_after_end(self):
        reservation = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=self.now
        )
        confirm_reservation(reservation.id_reserva, by_system=True, now=self.now)
        entry_qr = get_active_qr_for_reservation(reservation.id_reserva, self.tenant)
        validate_scanned_qr(entry_qr.token_qr, self.landlord, now=self.start_dt)

        exit_qr = get_active_qr_for_reservation(reservation.id_reserva, self.tenant)
        exit_result = validate_scanned_qr(
            exit_qr.token_qr,
            self.landlord,
            now=self.end_dt + timedelta(hours=3),
        )
        self.assertEqual(exit_result['verificacion']['minutos_retraso'], 170)
        self.assertEqual(exit_result['verificacion']['monto_penalizacion'], '1700.00')

    def test_validate_qr_error_conditions_400_403_404_409_410(self):
        reservation = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=self.now
        )
        confirm_reservation(reservation.id_reserva, by_system=True, now=self.now)
        entry_qr = get_active_qr_for_reservation(reservation.id_reserva, self.tenant)

        with self.assertRaises(DomainValidationError):
            validate_scanned_qr('xyz', self.landlord, now=self.start_dt)

        with self.assertRaises(NotFoundError):
            validate_scanned_qr('0' * 64, self.landlord, now=self.start_dt)

        for unauthorized in (self.other_landlord, self.pending_landlord, self.tenant):
            with self.assertRaises(PermissionDeniedError):
                validate_scanned_qr(entry_qr.token_qr, unauthorized, now=self.start_dt)

        # Expired entry QR (now > fecha_hora_fin) -> GoneError (410) without marking usado
        with self.assertRaises(GoneError):
            validate_scanned_qr(entry_qr.token_qr, self.landlord, now=self.end_dt + timedelta(minutes=1))
        entry_qr.refresh_from_db()
        self.assertFalse(entry_qr.usado)

        # Cancelled reservation -> ConflictError (409)
        reservation.estado = ReservationStatus.CANCELADA
        reservation.save(update_fields=['estado'])
        with self.assertRaises(ConflictError):
            validate_scanned_qr(entry_qr.token_qr, self.landlord, now=self.start_dt)
        with self.assertRaises(ConflictError):
            get_active_qr_for_reservation(reservation.id_reserva, self.tenant)

        # Cross-tenant QR access -> PermissionDeniedError
        with self.assertRaises(PermissionDeniedError):
            get_active_qr_for_reservation(reservation.id_reserva, self.other_tenant)
