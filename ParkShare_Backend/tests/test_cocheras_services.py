from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    Disponibilidad,
    FotoCochera,
    GarageStatus,
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
    create_garage,
    evaluate_garage_realtime_availability,
    get_active_reservations_count,
    get_garage_detail_for_user,
    get_garage_rating_summary,
    list_landlord_garages,
    search_nearby_garages,
    update_garage,
    validate_garage_publication,
)


def _make_user(
    email: str,
    role: str,
    status: str = AccountStatus.ACTIVA,
) -> Usuario:
    extra = {'cuenta_cobro': '012180001234567890'} if role == UserRole.ARRENDADOR else {
        'placas': 'XYZ-123-A',
        'modelo_vehiculo': 'Mazda 3',
    }
    if role == UserRole.ADMIN:
        extra = {}
    return Usuario.objects.create_user(
        nombre_completo='Usuario Prueba ParkShare',
        correo=email,
        telefono='5511223344',
        password='MiPasswordSegura123!',
        fecha_nacimiento=date(1990, 5, 10),
        rol=role,
        estado_cuenta=status,
        **extra,
    )


def _valid_garage_payload() -> dict:
    return {
        'nombre': 'Cochera Los Pinos',
        'direccion': 'Av. Insurgentes Sur 1450, Col. Actipan, C.P. 03230',
        'latitud': Decimal('19.37124500'),
        'longitud': Decimal('-99.17834200'),
        'metros_cuadrados': Decimal('35.50'),
        'espacios_totales': 2,
        'tarifa_hora': Decimal('45.00'),
        'tipo_vehiculo': VehicleType.AMBOS,
        'fotos': [
            'https://storage.parkshare.mx/cocheras/pinos_1.jpg',
            'https://storage.parkshare.mx/cocheras/pinos_2.webp',
        ],
        'disponibilidades': [
            {'dia_semana': 'L', 'hora_inicio': '07:00', 'hora_fin': '22:00'},
            {'dia_semana': 'M', 'hora_inicio': '07:00', 'hora_fin': '22:00'},
        ],
    }


class CocherasCreateUpdateServiceTests(TestCase):
    def setUp(self):
        self.active_landlord = _make_user('activo@gmail.com', UserRole.ARRENDADOR, AccountStatus.ACTIVA)
        self.other_landlord = _make_user('otro@gmail.com', UserRole.ARRENDADOR, AccountStatus.ACTIVA)
        self.pending_landlord = _make_user('pendiente@gmail.com', UserRole.ARRENDADOR, AccountStatus.PENDIENTE)
        self.rejected_landlord = _make_user('rechazado@gmail.com', UserRole.ARRENDADOR, AccountStatus.RECHAZADA)
        self.tenant = _make_user('arrendatario@outlook.com', UserRole.ARRENDATARIO, AccountStatus.ACTIVA)
        self.admin = _make_user('admin@parkshare.mx', UserRole.ADMIN, AccountStatus.ACTIVA)

    def test_create_garage_atomically_and_force_pending_status(self):
        payload = _valid_garage_payload()
        payload['estado_cochera'] = GarageStatus.APROBADA  # Must be ignored/forced to PENDIENTE

        garage = create_garage(self.active_landlord, payload)

        self.assertEqual(garage.estado_cochera, GarageStatus.PENDIENTE)
        self.assertEqual(garage.arrendador_id, self.active_landlord.id_usuario)
        self.assertEqual(FotoCochera.objects.filter(cochera=garage).count(), 2)
        self.assertEqual(Disponibilidad.objects.filter(cochera=garage).count(), 2)

    def test_create_garage_allows_contiguous_non_overlapping_slots_same_day(self):
        payload = _valid_garage_payload()
        payload['disponibilidades'] = [
            {'dia_semana': 'L', 'hora_inicio': '08:00', 'hora_fin': '12:00'},
            {'dia_semana': 'L', 'hora_inicio': '12:00', 'hora_fin': '18:00'},
        ]
        garage = create_garage(self.active_landlord, payload)
        self.assertEqual(Disponibilidad.objects.filter(cochera=garage, dia_semana='L').count(), 2)

    def test_create_garage_blocks_non_active_landlord_and_tenant_rn08(self):
        for unauthorized in (self.pending_landlord, self.rejected_landlord, self.tenant):
            with self.assertRaises(PermissionDeniedError):
                create_garage(unauthorized, _valid_garage_payload())

    def test_create_garage_rejects_overlapping_slots_or_empty_photos_or_slots(self):
        no_photos = _valid_garage_payload()
        no_photos['fotos'] = []
        with self.assertRaises(DomainValidationError):
            create_garage(self.active_landlord, no_photos)
        self.assertEqual(Cochera.objects.count(), 0)

        no_slots = _valid_garage_payload()
        no_slots['disponibilidades'] = []
        with self.assertRaises(DomainValidationError):
            create_garage(self.active_landlord, no_slots)

        overlapping = _valid_garage_payload()
        overlapping['disponibilidades'] = [
            {'dia_semana': 'L', 'hora_inicio': '08:00', 'hora_fin': '14:00'},
            {'dia_semana': 'L', 'hora_inicio': '12:00', 'hora_fin': '18:00'},
        ]
        with self.assertRaises(DomainValidationError):
            create_garage(self.active_landlord, overlapping)
        self.assertEqual(Cochera.objects.count(), 0)

    def test_admin_validate_garage_publication_state_machine_rn10(self):
        garage = create_garage(self.active_landlord, _valid_garage_payload())

        with self.assertRaises(PermissionDeniedError):
            validate_garage_publication(garage.id_cochera, self.active_landlord, GarageStatus.APROBADA)

        result = validate_garage_publication(garage.id_cochera, self.admin, GarageStatus.APROBADA)
        garage.refresh_from_db()
        self.assertEqual(garage.estado_cochera, GarageStatus.APROBADA)
        self.assertEqual(result['estado_cochera'], GarageStatus.APROBADA)

        with self.assertRaises(ConflictError):
            validate_garage_publication(garage.id_cochera, self.admin, GarageStatus.RECHAZADA, reason='Tarde')

        garage2 = create_garage(self.active_landlord, _valid_garage_payload())
        with self.assertRaises(DomainValidationError):
            validate_garage_publication(garage2.id_cochera, self.admin, GarageStatus.RECHAZADA, reason='')

        rejected = validate_garage_publication(
            garage2.id_cochera,
            self.admin,
            GarageStatus.RECHAZADA,
            reason='Falta iluminación adecuada.',
        )
        garage2.refresh_from_db()
        self.assertEqual(garage2.estado_cochera, GarageStatus.RECHAZADA)
        self.assertEqual(rejected['motivo'], 'Falta iluminación adecuada.')

        with self.assertRaises(NotFoundError):
            validate_garage_publication(999999, self.admin, GarageStatus.APROBADA)

    def test_update_garage_ownership_state_transitions_and_atomic_slots(self):
        garage = create_garage(self.active_landlord, _valid_garage_payload())

        # Owner cannot self-approve from PENDIENTE -> APROBADA
        with self.assertRaises(PermissionDeniedError):
            update_garage(garage.id_cochera, self.active_landlord, {'estado_cochera': GarageStatus.APROBADA})

        # Owner cannot pause PENDIENTE -> INACTIVA
        with self.assertRaises(ConflictError):
            update_garage(garage.id_cochera, self.active_landlord, {'estado_cochera': GarageStatus.INACTIVA})

        validate_garage_publication(garage.id_cochera, self.admin, GarageStatus.APROBADA)

        # Other landlord cannot modify
        with self.assertRaises(PermissionDeniedError):
            update_garage(garage.id_cochera, self.other_landlord, {'tarifa_hora': Decimal('99.00')})

        # Owner toggles APROBADA -> INACTIVA and updates rate + schedule atomically
        updated = update_garage(
            garage.id_cochera,
            self.active_landlord,
            {
                'tarifa_hora': Decimal('50.00'),
                'estado_cochera': GarageStatus.INACTIVA,
                'disponibilidades': [
                    {'dia_semana': 'S', 'hora_inicio': '09:00', 'hora_fin': '18:00'},
                ],
            },
        )
        self.assertEqual(updated.estado_cochera, GarageStatus.INACTIVA)
        self.assertEqual(updated.tarifa_hora, Decimal('50.00'))
        self.assertEqual(updated.disponibilidades.count(), 1)

        # Owner reactivates INACTIVA -> APROBADA
        reactivated = update_garage(
            garage.id_cochera,
            self.active_landlord,
            {'estado_cochera': GarageStatus.APROBADA},
        )
        self.assertEqual(reactivated.estado_cochera, GarageStatus.APROBADA)

    def test_visibility_and_landlord_listing(self):
        g_pending = create_garage(self.active_landlord, _valid_garage_payload())
        g_approved = create_garage(self.active_landlord, _valid_garage_payload())
        validate_garage_publication(g_approved.id_cochera, self.admin, GarageStatus.APROBADA)

        own_list = list_landlord_garages(self.active_landlord)
        self.assertEqual(len(own_list), 2)
        other_list = list_landlord_garages(self.other_landlord)
        self.assertEqual(len(other_list), 0)

        with self.assertRaises(NotFoundError):
            get_garage_detail_for_user(g_pending.id_cochera, self.tenant)

        self.assertEqual(get_garage_detail_for_user(g_pending.id_cochera, self.active_landlord).id_cochera, g_pending.id_cochera)
        self.assertEqual(get_garage_detail_for_user(g_pending.id_cochera, self.admin).id_cochera, g_pending.id_cochera)
        self.assertEqual(get_garage_detail_for_user(g_approved.id_cochera, self.tenant).id_cochera, g_approved.id_cochera)


class CocherasGeospatialAndAvailabilityTests(TestCase):
    def setUp(self):
        self.landlord = _make_user('geo@gmail.com', UserRole.ARRENDADOR, AccountStatus.ACTIVA)
        self.tz = ZoneInfo('America/Mexico_City')

        # 1. Very close, expensive, open on Monday 10:00 (2 spaces)
        self.g_close = Cochera.objects.create(
            arrendador=self.landlord,
            nombre='Cochera Cercana',
            direccion='Angel de la Independencia',
            latitud=Decimal('19.42702500'),
            longitud=Decimal('-99.16766500'),
            metros_cuadrados=Decimal('40.00'),
            espacios_totales=2,
            tarifa_hora=Decimal('80.00'),
            tipo_vehiculo=VehicleType.AUTO,
            estado_cochera=GarageStatus.APROBADA,
        )
        FotoCochera.objects.create(cochera=self.g_close, url_foto='https://cdn.parkshare.mx/close.jpg')
        Disponibilidad.objects.create(
            cochera=self.g_close,
            dia_semana=WeekdayChoice.L,
            hora_inicio=time(7, 0),
            hora_fin=time(22, 0),
        )

        # 2. Medium distance (~3.66 km), cheapest, open on Monday 10:00 (5 spaces), MOTO only
        self.g_cheap = Cochera.objects.create(
            arrendador=self.landlord,
            nombre='Cochera Economica Moto',
            direccion='Zocalo CDMX',
            latitud=Decimal('19.43260800'),
            longitud=Decimal('-99.13320800'),
            metros_cuadrados=Decimal('25.00'),
            espacios_totales=5,
            tarifa_hora=Decimal('25.00'),
            tipo_vehiculo=VehicleType.MOTO,
            estado_cochera=GarageStatus.APROBADA,
        )
        FotoCochera.objects.create(cochera=self.g_cheap, url_foto='https://cdn.parkshare.mx/cheap.png')
        Disponibilidad.objects.create(
            cochera=self.g_cheap,
            dia_semana=WeekdayChoice.L,
            hora_inicio=time(8, 0),
            hora_fin=time(20, 0),
        )

        # 3. Closed on Monday 10:00 (only open Tuesday), AMBOS, mid-price
        self.g_closed_now = Cochera.objects.create(
            arrendador=self.landlord,
            nombre='Cochera Martes',
            direccion='Colonia Roma',
            latitud=Decimal('19.41944400'),
            longitud=Decimal('-99.16277800'),
            metros_cuadrados=Decimal('30.00'),
            espacios_totales=10,
            tarifa_hora=Decimal('40.00'),
            tipo_vehiculo=VehicleType.AMBOS,
            estado_cochera=GarageStatus.APROBADA,
        )
        FotoCochera.objects.create(cochera=self.g_closed_now, url_foto='https://cdn.parkshare.mx/roma.webp')
        Disponibilidad.objects.create(
            cochera=self.g_closed_now,
            dia_semana=WeekdayChoice.M,
            hora_inicio=time(8, 0),
            hora_fin=time(20, 0),
        )

        # 4. Non-approved garages must never appear in search
        Cochera.objects.create(
            arrendador=self.landlord,
            nombre='Cochera Pendiente',
            direccion='Mismo Punto',
            latitud=Decimal('19.42702500'),
            longitud=Decimal('-99.16766500'),
            metros_cuadrados=Decimal('20.00'),
            espacios_totales=2,
            tarifa_hora=Decimal('10.00'),
            tipo_vehiculo=VehicleType.AMBOS,
            estado_cochera=GarageStatus.PENDIENTE,
        )
        Cochera.objects.create(
            arrendador=self.landlord,
            nombre='Cochera Inactiva',
            direccion='Mismo Punto',
            latitud=Decimal('19.42702500'),
            longitud=Decimal('-99.16766500'),
            metros_cuadrados=Decimal('20.00'),
            espacios_totales=2,
            tarifa_hora=Decimal('10.00'),
            tipo_vehiculo=VehicleType.AMBOS,
            estado_cochera=GarageStatus.INACTIVA,
        )

    def test_identical_coordinates_distance_zero_without_acos_domain_error(self):
        # 2026-11-16 is Monday ('L')
        monday_10am = datetime(2026, 11, 16, 10, 0, tzinfo=self.tz)
        results = search_nearby_garages(
            lat=19.42702500,
            lng=-99.16766500,
            order_by='cercania',
            current_dt=monday_10am,
        )
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].id_cochera, self.g_close.id_cochera)
        self.assertEqual(results[0].distancia_km, 0.0)
        # Angel to Roma is ~0.99 km; Angel to Zocalo is ~3.66 km
        self.assertEqual(results[1].id_cochera, self.g_closed_now.id_cochera)
        self.assertEqual(results[2].id_cochera, self.g_cheap.id_cochera)
        self.assertGreater(results[2].distancia_km, 3.0)
        self.assertLess(results[2].distancia_km, 4.5)

    def test_search_sorting_by_price_and_availability_and_filters(self):
        monday_10am = datetime(2026, 11, 16, 10, 0, tzinfo=self.tz)

        by_price = search_nearby_garages(
            lat=19.42702500,
            lng=-99.16766500,
            order_by='precio',
            current_dt=monday_10am,
        )
        self.assertEqual(
            [g.id_cochera for g in by_price],
            [self.g_cheap.id_cochera, self.g_closed_now.id_cochera, self.g_close.id_cochera],
        )

        # g_cheap has 5 open spaces, g_close has 2 open spaces, g_closed_now is closed (0 spaces now)
        by_avail = search_nearby_garages(
            lat=19.42702500,
            lng=-99.16766500,
            order_by='disponibilidad',
            current_dt=monday_10am,
        )
        self.assertEqual(
            [g.id_cochera for g in by_avail],
            [self.g_cheap.id_cochera, self.g_close.id_cochera, self.g_closed_now.id_cochera],
        )
        self.assertTrue(by_avail[0].abierta_ahora)
        self.assertEqual(by_avail[0].espacios_disponibles_ahora, 5)
        self.assertFalse(by_avail[2].abierta_ahora)
        self.assertEqual(by_avail[2].espacios_disponibles_ahora, 0)

        # Vehicle type filter: MOTO includes MOTO and AMBOS, excludes AUTO
        moto_only = search_nearby_garages(
            lat=19.42702500,
            lng=-99.16766500,
            vehicle_type='MOTO',
            current_dt=monday_10am,
        )
        moto_ids = {g.id_cochera for g in moto_only}
        self.assertEqual(moto_ids, {self.g_cheap.id_cochera, self.g_closed_now.id_cochera})

        # Vehicle type filter: AMBOS returns only AMBOS garages
        ambos_only = search_nearby_garages(
            lat=19.42702500,
            lng=-99.16766500,
            vehicle_type='AMBOS',
            current_dt=monday_10am,
        )
        self.assertEqual({g.id_cochera for g in ambos_only}, {self.g_closed_now.id_cochera})

        # Radius filter: 2 km excludes g_cheap (~3.66 km)
        within_2km = search_nearby_garages(
            lat=19.42702500,
            lng=-99.16766500,
            radius_km=2.0,
            current_dt=monday_10am,
        )
        self.assertEqual({g.id_cochera for g in within_2km}, {self.g_close.id_cochera, self.g_closed_now.id_cochera})

    def test_realtime_availability_and_decoupled_hooks(self):
        monday_10am = datetime(2026, 11, 16, 10, 0, tzinfo=self.tz)
        monday_23pm = datetime(2026, 11, 16, 23, 0, tzinfo=self.tz)

        is_open, spaces = evaluate_garage_realtime_availability(self.g_close, monday_10am)
        self.assertTrue(is_open)
        self.assertEqual(spaces, 2)

        is_open_late, spaces_late = evaluate_garage_realtime_availability(self.g_close, monday_23pm)
        self.assertFalse(is_open_late)
        self.assertEqual(spaces_late, 0)

        # Verify 23:59:59 end-of-day boundary ignores sub-second microseconds
        Disponibilidad.objects.create(
            cochera=self.g_close,
            dia_semana=WeekdayChoice.D,
            hora_inicio=time(0, 0),
            hora_fin=time(23, 59, 59),
        )
        sunday_end_with_micros = datetime(2026, 11, 22, 23, 59, 59, 500000, tzinfo=self.tz)
        is_open_eod, _ = evaluate_garage_realtime_availability(self.g_close, sunday_end_with_micros)
        self.assertTrue(is_open_eod)

        self.assertEqual(get_active_reservations_count(self.g_close), 0)
        self.assertEqual(
            get_garage_rating_summary(self.g_close),
            {'calificacion_promedio': None, 'total_resenas': 0},
        )
