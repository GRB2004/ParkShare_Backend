from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    Disponibilidad,
    FotoCochera,
    GarageStatus,
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
    NotFoundError,
    PermissionDeniedError,
)
from ParkShare_Backend.services.cochera_service import (
    evaluate_garage_realtime_availability,
    search_nearby_garages,
)
from ParkShare_Backend.services.reserva_service import (
    calculate_base_amount,
    calculate_cancellation_penalty,
    calculate_late_surcharge,
    cancel_reservation,
    confirm_reservation,
    create_reservation,
    finish_reservation,
    get_reservation_detail_for_user,
    list_reservations_for_user,
    start_reservation_usage,
    transition_reservation_state,
    validate_reservation_duration,
)

MEXICO_TZ = ZoneInfo('America/Mexico_City')


def _create_user(email: str, role: str, status: str = AccountStatus.ACTIVA) -> Usuario:
    extra = {'cuenta_cobro': '012180001234567890'} if role == UserRole.ARRENDADOR else {
        'placas': 'ABC-123-D',
        'modelo_vehiculo': 'Nissan Versa 2022',
    }
    if role == UserRole.ADMIN:
        extra = {}
    return Usuario.objects.create_user(
        nombre_completo='Usuario Prueba ParkShare',
        correo=email,
        telefono='5512345678',
        password='MiPasswordSegura123!',
        fecha_nacimiento=date(1990, 5, 10),
        rol=role,
        estado_cuenta=status,
        **extra,
    )


def _create_garage_with_schedule(
    landlord: Usuario,
    spaces: int = 2,
    rate: str = '45.00',
    status: str = GarageStatus.APROBADA,
    slots: list[tuple[str, time, time]] | None = None,
) -> Cochera:
    garage = Cochera.objects.create(
        arrendador=landlord,
        nombre='Cochera Los Pinos',
        direccion='Av. Insurgentes Sur 1450',
        latitud=Decimal('19.37124500'),
        longitud=Decimal('-99.17834200'),
        metros_cuadrados=Decimal('35.50'),
        espacios_totales=spaces,
        tarifa_hora=Decimal(rate),
        tipo_vehiculo=VehicleType.AMBOS,
        estado_cochera=status,
    )
    FotoCochera.objects.create(cochera=garage, url_foto='https://cdn.parkshare.mx/g.jpg')
    default_slots = slots if slots is not None else [
        (day, time(8, 0), time(22, 0)) for day in WeekdayChoice.values
    ]
    for day, start_t, end_t in default_slots:
        Disponibilidad.objects.create(
            cochera=garage,
            dia_semana=day,
            hora_inicio=start_t,
            hora_fin=end_t,
        )
    return garage


class ReservaPureRulesAndPenaltiesTests(TestCase):
    def test_validate_reservation_duration_rn01(self):
        base = datetime(2026, 11, 25, 10, 0, tzinfo=MEXICO_TZ)

        # Valid boundaries and blocks: 30 min, 90 min, 24 h (1440 min)
        self.assertEqual(validate_reservation_duration(base, base + timedelta(minutes=30)), 30)
        self.assertEqual(validate_reservation_duration(base, base + timedelta(minutes=90)), 90)
        self.assertEqual(validate_reservation_duration(base, base + timedelta(hours=24)), 1440)

        # Invalid: reversed/equal, 15m (< 30m), 45m (non-30m block), 24h30m (> 24h), residual seconds
        for bad_end in (
            base,
            base - timedelta(minutes=30),
            base + timedelta(minutes=15),
            base + timedelta(minutes=45),
            base + timedelta(hours=24, minutes=30),
            base + timedelta(minutes=30, seconds=15),
        ):
            with self.assertRaises(DomainValidationError):
                validate_reservation_duration(base, bad_end)

    def test_calculate_base_amount(self):
        self.assertEqual(calculate_base_amount(Decimal('45.00'), 90), Decimal('67.50'))
        self.assertEqual(calculate_base_amount(Decimal('45.00'), 30), Decimal('22.50'))
        self.assertEqual(calculate_base_amount(Decimal('33.33'), 30), Decimal('16.67'))

    def test_calculate_late_surcharge_rn02_and_rn03(self):
        end_dt = datetime(2026, 11, 25, 12, 0, 0, tzinfo=MEXICO_TZ)

        # Before end or within 10-minute tolerance -> 0 min, $0.00
        self.assertEqual(
            calculate_late_surcharge(end_dt, end_dt - timedelta(minutes=5)),
            (0, Decimal('0.00')),
        )
        self.assertEqual(
            calculate_late_surcharge(end_dt, end_dt + timedelta(minutes=10)),
            (0, Decimal('0.00')),
        )

        # 10 min 01 s -> 1 min -> $10.00
        self.assertEqual(
            calculate_late_surcharge(end_dt, end_dt + timedelta(minutes=10, seconds=1)),
            (1, Decimal('10.00')),
        )

        # 20 min 00 s (10 min beyond tolerance) -> 10 min -> $100.00
        self.assertEqual(
            calculate_late_surcharge(end_dt, end_dt + timedelta(minutes=20)),
            (10, Decimal('100.00')),
        )

    def test_calculate_cancellation_penalty_rn04_and_rn05(self):
        created = datetime(2026, 11, 25, 9, 0, 0, tzinfo=MEXICO_TZ)

        # SOLICITADA is always free even after 10 minutes
        self.assertEqual(
            calculate_cancellation_penalty(
                ReservationStatus.SOLICITADA,
                created,
                created + timedelta(minutes=30),
            ),
            (False, Decimal('0.00')),
        )

        # CONFIRMADA <= 10m00s -> $0.00 (RN-04)
        self.assertEqual(
            calculate_cancellation_penalty(
                ReservationStatus.CONFIRMADA,
                created,
                created + timedelta(minutes=10),
            ),
            (False, Decimal('0.00')),
        )

        # CONFIRMADA > 10m00s -> $100.00 (RN-05)
        self.assertEqual(
            calculate_cancellation_penalty(
                ReservationStatus.CONFIRMADA,
                created,
                created + timedelta(minutes=10, seconds=1),
            ),
            (True, Decimal('100.00')),
        )


class ReservaTransactionalServicesTests(TestCase):
    def setUp(self):
        self.landlord = _create_user('landlord.rsv@gmail.com', UserRole.ARRENDADOR)
        self.other_landlord = _create_user('other.landlord@gmail.com', UserRole.ARRENDADOR)
        self.tenant = _create_user('tenant.rsv@outlook.com', UserRole.ARRENDATARIO)
        self.other_tenant = _create_user('tenant2.rsv@outlook.com', UserRole.ARRENDATARIO)
        self.admin = _create_user('admin.rsv@parkshare.mx', UserRole.ADMIN)
        self.now = datetime(2026, 11, 23, 8, 0, tzinfo=MEXICO_TZ)  # Monday ('L')

    def test_create_reservation_schedule_capacity_contiguous_and_midnight(self):
        garage = _create_garage_with_schedule(self.landlord, spaces=1, rate='45.00')
        start = datetime(2026, 11, 23, 10, 0, tzinfo=MEXICO_TZ)
        end = datetime(2026, 11, 23, 11, 30, tzinfo=MEXICO_TZ)

        r1 = create_reservation(self.tenant, garage.id_cochera, start, end, now=self.now)
        self.assertEqual(r1.estado, ReservationStatus.SOLICITADA)
        self.assertEqual(r1.monto_base, Decimal('67.50'))

        # Overlapping fails with ConflictError (1/1 capacity full)
        with self.assertRaises(ConflictError):
            create_reservation(
                self.other_tenant,
                garage.id_cochera,
                datetime(2026, 11, 23, 11, 0, tzinfo=MEXICO_TZ),
                datetime(2026, 11, 23, 12, 0, tzinfo=MEXICO_TZ),
                now=self.now,
            )

        # Contiguous/later slot [11:30, 12:30) ALSO fails while r1 has not been vacated or cancelled
        with self.assertRaises(ConflictError):
            create_reservation(
                self.other_tenant,
                garage.id_cochera,
                datetime(2026, 11, 23, 11, 30, tzinfo=MEXICO_TZ),
                datetime(2026, 11, 23, 12, 30, tzinfo=MEXICO_TZ),
                now=self.now,
            )

        # Even if r1 is confirmed and in use (e.g., 13:30-14:30), User B still cannot book 14:30-15:30
        confirm_reservation(r1.id_reserva, by_system=True, now=self.now)
        with self.assertRaises(ConflictError):
            create_reservation(
                self.other_tenant,
                garage.id_cochera,
                datetime(2026, 11, 23, 11, 30, tzinfo=MEXICO_TZ),
                datetime(2026, 11, 23, 12, 30, tzinfo=MEXICO_TZ),
                now=self.now,
            )

        # Once r1 is cancelled (or finalized), the space is freed and User B can book [11:30, 12:30)
        cancel_reservation(r1.id_reserva, self.tenant, now=self.now)
        r2 = create_reservation(
            self.other_tenant,
            garage.id_cochera,
            datetime(2026, 11, 23, 11, 30, tzinfo=MEXICO_TZ),
            datetime(2026, 11, 23, 12, 30, tzinfo=MEXICO_TZ),
            now=self.now,
        )
        self.assertEqual(r2.monto_base, Decimal('45.00'))

        # Once User B vacates (EN_USO -> FINALIZADA), self.tenant can book again
        confirm_reservation(r2.id_reserva, by_system=True, now=self.now)
        start_reservation_usage(
            r2.id_reserva,
            self.landlord,
            now=datetime(2026, 11, 23, 11, 35, tzinfo=MEXICO_TZ),
        )
        finish_reservation(
            r2.id_reserva,
            self.landlord,
            real_checkout_dt=datetime(2026, 11, 23, 12, 20, tzinfo=MEXICO_TZ),
            now=datetime(2026, 11, 23, 12, 20, tzinfo=MEXICO_TZ),
        )
        r3 = create_reservation(self.tenant, garage.id_cochera, start, end, now=self.now)
        self.assertEqual(r3.estado, ReservationStatus.SOLICITADA)

        # Cancel r3 before creating overnight reservation so self.tenant has no active reservation (RN-08)
        cancel_reservation(r3.id_reserva, self.tenant, now=self.now)

        # Overnight crossing Friday -> Saturday (2026-11-27 22:00 to 2026-11-28 02:00)
        night_garage = _create_garage_with_schedule(
            self.landlord,
            spaces=1,
            slots=[
                (WeekdayChoice.V, time(18, 0), time(23, 59, 59)),
                (WeekdayChoice.S, time(0, 0), time(6, 0)),
            ],
        )
        r_night = create_reservation(
            self.tenant,
            night_garage.id_cochera,
            datetime(2026, 11, 27, 22, 0, tzinfo=MEXICO_TZ),
            datetime(2026, 11, 28, 2, 0, tzinfo=MEXICO_TZ),
            now=self.now,
        )
        self.assertEqual(r_night.monto_base, Decimal('180.00'))

    def test_create_reservation_rejects_past_outside_schedule_unapproved_and_self_reserve(self):
        garage = _create_garage_with_schedule(self.landlord, spaces=2)

        # Past start time
        with self.assertRaises(DomainValidationError):
            create_reservation(
                self.tenant,
                garage.id_cochera,
                self.now - timedelta(minutes=30),
                self.now + timedelta(minutes=30),
                now=self.now,
            )

        # Outside schedule (garage opens 08:00-22:00)
        with self.assertRaises(DomainValidationError):
            create_reservation(
                self.tenant,
                garage.id_cochera,
                datetime(2026, 11, 23, 21, 30, tzinfo=MEXICO_TZ),
                datetime(2026, 11, 23, 22, 30, tzinfo=MEXICO_TZ),
                now=self.now,
            )

        # Non-approved garage
        pending_garage = _create_garage_with_schedule(
            self.landlord, status=GarageStatus.PENDIENTE
        )
        with self.assertRaises(ConflictError):
            create_reservation(
                self.tenant,
                pending_garage.id_cochera,
                datetime(2026, 11, 23, 10, 0, tzinfo=MEXICO_TZ),
                datetime(2026, 11, 23, 11, 0, tzinfo=MEXICO_TZ),
                now=self.now,
            )

        # Non-tenant role rejected
        with self.assertRaises(PermissionDeniedError):
            create_reservation(
                self.other_landlord,
                garage.id_cochera,
                datetime(2026, 11, 23, 10, 0, tzinfo=MEXICO_TZ),
                datetime(2026, 11, 23, 11, 0, tzinfo=MEXICO_TZ),
                now=self.now,
            )

        # Non-existent garage
        with self.assertRaises(NotFoundError):
            create_reservation(
                self.tenant,
                999999,
                datetime(2026, 11, 23, 10, 0, tzinfo=MEXICO_TZ),
                datetime(2026, 11, 23, 11, 0, tzinfo=MEXICO_TZ),
                now=self.now,
            )

    def test_state_machine_transitions_permissions_and_terminal_guards(self):
        garage = _create_garage_with_schedule(self.landlord, spaces=2, rate='40.00')
        start = datetime(2026, 11, 23, 10, 0, tzinfo=MEXICO_TZ)
        end = datetime(2026, 11, 23, 12, 0, tzinfo=MEXICO_TZ)

        reservation = create_reservation(self.tenant, garage.id_cochera, start, end, now=self.now)

        # Forbidden jump SOLICITADA -> EN_USO -> ConflictError
        with self.assertRaises(ConflictError):
            transition_reservation_state(
                reservation.id_reserva, self.landlord, ReservationStatus.EN_USO, now=start
            )

        # Tenant cannot confirm -> PermissionDeniedError
        with self.assertRaises(PermissionDeniedError):
            transition_reservation_state(
                reservation.id_reserva, self.tenant, ReservationStatus.CONFIRMADA
            )

        # Confirm by service / admin
        confirmed = confirm_reservation(reservation.id_reserva, actor=self.admin)
        self.assertEqual(confirmed.estado, ReservationStatus.CONFIRMADA)

        # Cannot expire before end_dt -> ConflictError
        with self.assertRaises(ConflictError):
            transition_reservation_state(
                reservation.id_reserva,
                self.landlord,
                ReservationStatus.EXPIRADA,
                now=end - timedelta(minutes=1),
            )

        # Unrelated landlord cannot start usage -> PermissionDeniedError
        with self.assertRaises(PermissionDeniedError):
            transition_reservation_state(
                reservation.id_reserva,
                self.other_landlord,
                ReservationStatus.EN_USO,
                now=start,
            )

        # Owner landlord starts usage
        in_use = start_reservation_usage(reservation.id_reserva, self.landlord, now=start)
        self.assertEqual(in_use.estado, ReservationStatus.EN_USO)

        # Cannot cancel while EN_USO -> ConflictError
        with self.assertRaises(ConflictError):
            cancel_reservation(reservation.id_reserva, self.tenant, now=start)

        # Live detail in tolerance vs late
        _, info_tol = get_reservation_detail_for_user(
            reservation.id_reserva,
            self.tenant,
            now=end + timedelta(minutes=8),
        )
        self.assertTrue(info_tol['tiempo_en_uso']['en_tolerancia'])
        self.assertEqual(info_tol['desglose_montos']['recargo_estimado_actual'], Decimal('0.00'))

        # Finish 20 minutes after end -> 10m beyond tolerance = $100.00 penalty
        finished = finish_reservation(
            reservation.id_reserva,
            self.landlord,
            real_checkout_dt=end + timedelta(minutes=20),
        )
        self.assertEqual(finished.estado, ReservationStatus.FINALIZADA)
        self.assertEqual(finished.monto_penalizacion, Decimal('100.00'))
        self.assertEqual(finished.monto_total, Decimal('180.00'))

        # Terminal state rejects any further transition
        with self.assertRaises(ConflictError):
            cancel_reservation(finished.id_reserva, self.admin, now=end)

    def test_integration_with_cochera_realtime_occupancy(self):
        garage = _create_garage_with_schedule(self.landlord, spaces=50, rate='45.00')
        start = datetime(2026, 11, 23, 13, 30, tzinfo=MEXICO_TZ)
        end = datetime(2026, 11, 23, 14, 30, tzinfo=MEXICO_TZ)

        is_open, free_before = evaluate_garage_realtime_availability(garage, self.now)
        self.assertTrue(is_open)
        self.assertEqual(free_before, 50)

        # Immediately at reservation creation (08:00, hours before 13:30 start), 50 decrements to 49
        r1 = create_reservation(self.tenant, garage.id_cochera, start, end, now=self.now)
        _, free_on_book = evaluate_garage_realtime_availability(garage, self.now)
        self.assertEqual(free_on_book, 49)

        search_res = search_nearby_garages(
            lat=19.37124500,
            lng=-99.17834200,
            current_dt=self.now,
        )
        self.assertEqual(search_res[0].espacios_disponibles_ahora, 49)

        confirm_reservation(r1.id_reserva, by_system=True, now=self.now)
        start_reservation_usage(r1.id_reserva, self.landlord, now=start + timedelta(minutes=5))

        # Even past end time [14:45], while not vacated/cancelled, space remains occupied (49)
        _, free_past_end_in_use = evaluate_garage_realtime_availability(
            garage, end + timedelta(minutes=15)
        )
        self.assertEqual(free_past_end_in_use, 49)

        # Once vacated (FINALIZADA), immediately increments from 49 back to 50
        finish_reservation(
            r1.id_reserva,
            self.landlord,
            real_checkout_dt=end + timedelta(minutes=15),
            now=end + timedelta(minutes=15),
        )
        _, free_after_vacate = evaluate_garage_realtime_availability(
            garage, end + timedelta(minutes=15)
        )
        self.assertEqual(free_after_vacate, 50)

    def test_list_reservations_for_user_views(self):
        garage = _create_garage_with_schedule(self.landlord, spaces=2)
        start = datetime(2026, 11, 23, 10, 0, tzinfo=MEXICO_TZ)
        r_done = create_reservation(
            self.tenant,
            garage.id_cochera,
            start + timedelta(hours=2),
            start + timedelta(hours=3),
            now=self.now,
        )
        cancel_reservation(r_done.id_reserva, self.tenant, now=self.now)
        r_active = create_reservation(
            self.tenant,
            garage.id_cochera,
            start,
            start + timedelta(minutes=60),
            now=self.now,
        )

        upcoming = list_reservations_for_user(self.tenant, view_filter='proximas')
        self.assertEqual([r.id_reserva for r in upcoming], [r_active.id_reserva])

        past = list_reservations_for_user(self.tenant, view_filter='anteriores')
        self.assertEqual([r.id_reserva for r in past], [r_done.id_reserva])

        with self.assertRaises(DomainValidationError):
            list_reservations_for_user(self.tenant, view_filter='en_uso')

    def test_rn08_single_active_reservation_per_tenant(self):
        garage1 = _create_garage_with_schedule(self.landlord, spaces=2)
        garage2 = _create_garage_with_schedule(self.other_landlord, spaces=2)
        start1 = datetime(2026, 11, 23, 10, 0, tzinfo=MEXICO_TZ)
        end1 = datetime(2026, 11, 23, 11, 0, tzinfo=MEXICO_TZ)
        start2 = datetime(2026, 11, 23, 12, 0, tzinfo=MEXICO_TZ)
        end2 = datetime(2026, 11, 23, 13, 0, tzinfo=MEXICO_TZ)

        # 1. First reservation in SOLICITADA
        r1 = create_reservation(self.tenant, garage1.id_cochera, start1, end1, now=self.now)
        self.assertEqual(r1.estado, ReservationStatus.SOLICITADA)

        # Attempt to create second reservation while r1 is SOLICITADA -> ConflictError
        with self.assertRaises(ConflictError) as cm:
            create_reservation(self.tenant, garage2.id_cochera, start2, end2, now=self.now)
        self.assertIn('Ya cuentas con una reservación activa', str(cm.exception))

        # 2. Transition r1 to CONFIRMADA -> still blocks second reservation
        confirm_reservation(r1.id_reserva, actor=self.admin)
        with self.assertRaises(ConflictError):
            create_reservation(self.tenant, garage1.id_cochera, start2, end2, now=self.now)

        # 3. Transition r1 to EN_USO -> still blocks second reservation
        start_reservation_usage(r1.id_reserva, actor=self.landlord, now=start1)
        with self.assertRaises(ConflictError):
            create_reservation(self.tenant, garage2.id_cochera, start2, end2, now=self.now)

        # 4. Finish r1 (FINALIZADA) -> tenant can now create another reservation
        finish_reservation(r1.id_reserva, actor=self.landlord, real_checkout_dt=end1)
        r2 = create_reservation(self.tenant, garage2.id_cochera, start2, end2, now=self.now)
        self.assertEqual(r2.estado, ReservationStatus.SOLICITADA)

        # 5. Cancel r2 (CANCELADA) -> tenant can now create r3
        cancel_reservation(r2.id_reserva, self.tenant, now=self.now)
        start3 = datetime(2026, 11, 23, 14, 0, tzinfo=MEXICO_TZ)
        end3 = datetime(2026, 11, 23, 15, 0, tzinfo=MEXICO_TZ)
        r3 = create_reservation(self.tenant, garage1.id_cochera, start3, end3, now=self.now)
        self.assertEqual(r3.estado, ReservationStatus.SOLICITADA)

        # 6. Expire r3 (EXPIRADA) -> tenant can now create r4
        confirm_reservation(r3.id_reserva, actor=self.admin)
        transition_reservation_state(
            r3.id_reserva,
            actor=self.landlord,
            target_status=ReservationStatus.EXPIRADA,
            now=end3 + timedelta(minutes=1),
        )
        start4 = datetime(2026, 11, 23, 16, 0, tzinfo=MEXICO_TZ)
        end4 = datetime(2026, 11, 23, 17, 0, tzinfo=MEXICO_TZ)
        r4 = create_reservation(self.tenant, garage1.id_cochera, start4, end4, now=self.now)
        self.assertEqual(r4.estado, ReservationStatus.SOLICITADA)

    def test_rn07_entry_tolerance_and_auto_cancellation_with_penalty(self):
        garage = _create_garage_with_schedule(self.landlord, spaces=2, rate='50.00')
        start = datetime(2026, 11, 23, 14, 0, tzinfo=MEXICO_TZ)
        end = datetime(2026, 11, 23, 16, 0, tzinfo=MEXICO_TZ)

        # Case 1: Entry exactly at tolerance boundary (+20m00s) -> Succeeds into EN_USO
        r1 = create_reservation(self.tenant, garage.id_cochera, start, end, now=self.now)
        confirm_reservation(r1.id_reserva, actor=self.admin)
        at_boundary = start + timedelta(minutes=20)
        started_r1 = start_reservation_usage(r1.id_reserva, actor=self.landlord, now=at_boundary)
        self.assertEqual(started_r1.estado, ReservationStatus.EN_USO)
        self.assertEqual(started_r1.monto_penalizacion, Decimal('0.00'))

        # Finish r1 so other tenant or tenant can create next
        finish_reservation(r1.id_reserva, actor=self.landlord, real_checkout_dt=start + timedelta(minutes=40))

        # Case 2: Entry 1 second late (+20m01s) -> Auto-cancelled with $100.00 penalty & ConflictError
        r2 = create_reservation(self.tenant, garage.id_cochera, start + timedelta(hours=2), end + timedelta(hours=2), now=self.now)
        confirm_reservation(r2.id_reserva, actor=self.admin)
        one_second_late = (start + timedelta(hours=2)) + timedelta(minutes=20, seconds=1)
        with self.assertRaises(ConflictError) as cm:
            transition_reservation_state(
                r2.id_reserva,
                actor=self.landlord,
                target_status=ReservationStatus.EN_USO,
                now=one_second_late,
            )
        self.assertEqual(cm.exception.error_code, 'LateEntryCancelled')
        self.assertIn('Se excedió la tolerancia de 20 minutos para la entrada', str(cm.exception))

        # Check DB persistence: must be CANCELADA with 100.00 penalty despite exception
        r2.refresh_from_db()
        self.assertEqual(r2.estado, ReservationStatus.CANCELADA)
        self.assertEqual(r2.monto_penalizacion, Decimal('100.00'))

        # Case 3: Entry at +21m -> Also auto-cancelled with $100.00 penalty
        r3 = create_reservation(self.tenant, garage.id_cochera, start + timedelta(hours=5), end + timedelta(hours=5), now=self.now)
        confirm_reservation(r3.id_reserva, actor=self.admin)
        twenty_one_late = (start + timedelta(hours=5)) + timedelta(minutes=21)
        with self.assertRaises(ConflictError) as cm3:
            start_reservation_usage(r3.id_reserva, actor=self.landlord, now=twenty_one_late)
        self.assertEqual(cm3.exception.error_code, 'LateEntryCancelled')

        r3.refresh_from_db()
        self.assertEqual(r3.estado, ReservationStatus.CANCELADA)
        self.assertEqual(r3.monto_penalizacion, Decimal('100.00'))

