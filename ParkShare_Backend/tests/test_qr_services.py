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
    list_active_qrs_for_simulation,
    simulate_qr_scan,
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

    def test_rn07_validate_scanned_entry_qr_tolerance_and_late_auto_cancellation(self):
        # 1. Entry at exact boundary (+20m00s) -> 200 OK, transitions to EN_USO, marks QR usado, issues SALIDA QR
        r1 = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=self.now
        )
        confirm_reservation(r1.id_reserva, by_system=True, now=self.now)
        qr1 = get_active_qr_for_reservation(r1.id_reserva, self.tenant)

        res1 = validate_scanned_qr(
            qr1.token_qr,
            self.landlord,
            now=self.start_dt + timedelta(minutes=20),
        )
        self.assertEqual(res1['tipo_qr'], QRCodeType.ENTRADA)
        self.assertEqual(res1['estado_reserva'], ReservationStatus.EN_USO)
        self.assertTrue(res1['qr_salida_emitido'])

        qr1.refresh_from_db()
        self.assertTrue(qr1.usado)
        r1.refresh_from_db()
        self.assertEqual(r1.estado, ReservationStatus.EN_USO)
        self.assertEqual(r1.monto_penalizacion, Decimal('0.00'))

        # 2. Entry 1 second late (+20m01s) -> ConflictError (LateEntryCancelled), CANCELADA with 100.00, entry QR NOT used, no SALIDA QR
        r2_start = self.start_dt + timedelta(hours=4)
        r2_end = self.end_dt + timedelta(hours=4)
        r2 = create_reservation(
            self.other_tenant, self.garage.id_cochera, r2_start, r2_end, now=self.now
        )
        confirm_reservation(r2.id_reserva, by_system=True, now=self.now)
        qr2 = get_active_qr_for_reservation(r2.id_reserva, self.other_tenant)

        with self.assertRaises(ConflictError) as cm:
            validate_scanned_qr(
                qr2.token_qr,
                self.landlord,
                now=r2_start + timedelta(minutes=20, seconds=1),
            )
        self.assertEqual(cm.exception.error_code, 'LateEntryCancelled')
        self.assertIn('Se excedió la tolerancia de 20 minutos para la entrada', str(cm.exception))

        # DB persistence check
        r2.refresh_from_db()
        self.assertEqual(r2.estado, ReservationStatus.CANCELADA)
        self.assertEqual(r2.monto_penalizacion, Decimal('100.00'))

        qr2.refresh_from_db()
        self.assertFalse(qr2.usado)
        self.assertFalse(
            CodigoQR.objects.filter(reserva=r2, tipo_qr=QRCodeType.SALIDA).exists()
        )

        # 3. Entry at +25m -> ConflictError (LateEntryCancelled)
        r3_start = self.start_dt + timedelta(hours=8)
        r3_end = self.end_dt + timedelta(hours=8)
        r3 = create_reservation(
            self.other_tenant, self.garage.id_cochera, r3_start, r3_end, now=self.now
        )
        confirm_reservation(r3.id_reserva, by_system=True, now=self.now)
        qr3 = get_active_qr_for_reservation(r3.id_reserva, self.other_tenant)

        with self.assertRaises(ConflictError) as cm3:
            validate_scanned_qr(
                qr3.token_qr,
                self.landlord,
                now=r3_start + timedelta(minutes=25),
            )
        self.assertEqual(cm3.exception.error_code, 'LateEntryCancelled')
        r3.refresh_from_db()
        self.assertEqual(r3.estado, ReservationStatus.CANCELADA)
        self.assertEqual(r3.monto_penalizacion, Decimal('100.00'))
        qr3.refresh_from_db()
        self.assertFalse(qr3.usado)

    def test_list_active_qrs_for_simulation_and_simulate_qr_scan(self):
        # 1. Create a future confirmed reservation (2 hours from self.now)
        r1 = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=self.now
        )
        confirm_reservation(r1.id_reserva, by_system=True, now=self.now)

        # Create a reservation on another landlord's garage (must be excluded)
        other_garage = _create_garage(self.other_landlord, rate='50.00')
        r_other = create_reservation(
            self.other_tenant,
            other_garage.id_cochera,
            self.start_dt + timedelta(hours=3),
            self.end_dt + timedelta(hours=3),
            now=self.now,
        )
        confirm_reservation(r_other.id_reserva, by_system=True, now=self.now)

        # Unauthorized callers rejected
        for bad_user in (self.tenant, self.pending_landlord):
            with self.assertRaises(PermissionDeniedError):
                list_active_qrs_for_simulation(bad_user, now=self.now)

        # Invalid qr_type filter -> DomainValidationError
        with self.assertRaises(DomainValidationError):
            list_active_qrs_for_simulation(self.landlord, qr_type='INVALIDO', now=self.now)

        # List active QRs for self.landlord -> only r1's ENTRADA QR
        active_list = list(list_active_qrs_for_simulation(self.landlord, now=self.now))
        self.assertEqual(len(active_list), 1)
        self.assertEqual(active_list[0].reserva_id, r1.id_reserva)
        self.assertEqual(active_list[0].tipo_qr, QRCodeType.ENTRADA)

        # With respect_window=True on future reservation (2h before start) -> ConflictError
        with self.assertRaises(ConflictError):
            simulate_qr_scan(
                self.landlord,
                reserva_id=r1.id_reserva,
                respect_window=True,
                now=self.now,
            )

        # Default respect_window=False on future reservation -> simulates ENTRADA using start_dt
        sim_entry = simulate_qr_scan(
            self.landlord,
            qr_type=QRCodeType.ENTRADA,
            now=self.now,
        )
        self.assertTrue(sim_entry['simulado'])
        self.assertEqual(sim_entry['tipo_qr'], QRCodeType.ENTRADA)
        self.assertEqual(sim_entry['estado_reserva'], ReservationStatus.EN_USO)
        self.assertTrue(sim_entry['qr_salida_emitido'])
        self.assertNotIn('token_qr', sim_entry)

        # Now active QRs for simulation has SALIDA for r1
        active_after_entry = list(
            list_active_qrs_for_simulation(self.landlord, qr_type='SALIDA', now=self.now)
        )
        self.assertEqual(len(active_after_entry), 1)
        self.assertEqual(active_after_entry[0].tipo_qr, QRCodeType.SALIDA)

        # Simulate SALIDA immediately by reserva_id -> transitions to FINALIZADA
        sim_exit = simulate_qr_scan(
            self.landlord,
            reserva_id=r1.id_reserva,
            now=self.now,
        )
        self.assertTrue(sim_exit['simulado'])
        self.assertEqual(sim_exit['tipo_qr'], QRCodeType.SALIDA)
        self.assertEqual(sim_exit['estado_reserva'], ReservationStatus.FINALIZADA)

        # No more active QRs for self.landlord -> NotFoundError (NoActiveQRToSimulate)
        with self.assertRaises(NotFoundError) as cm_empty:
            simulate_qr_scan(self.landlord, now=self.now)
        self.assertEqual(cm_empty.exception.error_code, 'NoActiveQRToSimulate')

        # Attempting to simulate another landlord's reservation -> PermissionDeniedError
        with self.assertRaises(PermissionDeniedError):
            simulate_qr_scan(self.landlord, reserva_id=r_other.id_reserva, now=self.now)

