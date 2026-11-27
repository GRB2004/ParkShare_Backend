from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    Disponibilidad,
    FotoCochera,
    GarageStatus,
    Reserva,
    ReservationStatus,
    UserRole,
    VehicleType,
    WeekdayChoice,
)

Usuario = get_user_model()
MEXICO_TZ = ZoneInfo('America/Mexico_City')


class ReservasAPITests(APITestCase):
    def setUp(self):
        self.password = 'MiPasswordSegura123!'
        self.landlord = Usuario.objects.create_user(
            nombre_completo='Carlos Eduardo Ramírez Pérez',
            correo='carlos.ramirez@gmail.com',
            telefono='5598765432',
            password=self.password,
            fecha_nacimiento=date(1985, 11, 20),
            rol=UserRole.ARRENDADOR,
            estado_cuenta=AccountStatus.ACTIVA,
            cuenta_cobro='012180001234567890',
        )
        self.other_landlord = Usuario.objects.create_user(
            nombre_completo='Laura Méndez Torres',
            correo='laura.mendez@gmail.com',
            telefono='5587654321',
            password=self.password,
            fecha_nacimiento=date(1988, 3, 15),
            rol=UserRole.ARRENDADOR,
            estado_cuenta=AccountStatus.ACTIVA,
            cuenta_cobro='012180009876543210',
        )
        self.tenant = Usuario.objects.create_user(
            nombre_completo='Ana Sofía Martínez López',
            correo='ana.martinez@outlook.com',
            telefono='5512345678',
            password=self.password,
            fecha_nacimiento=date(1998, 5, 14),
            rol=UserRole.ARRENDATARIO,
            estado_cuenta=AccountStatus.ACTIVA,
            placas='ABC-123-D',
            modelo_vehiculo='Nissan Versa 2022',
        )
        self.other_tenant = Usuario.objects.create_user(
            nombre_completo='Jorge Luis Hernández',
            correo='jorge.hernandez@outlook.com',
            telefono='5511223344',
            password=self.password,
            fecha_nacimiento=date(1995, 2, 10),
            rol=UserRole.ARRENDATARIO,
            estado_cuenta=AccountStatus.ACTIVA,
            placas='XYZ-789-A',
            modelo_vehiculo='Honda Civic 2023',
        )
        self.admin = Usuario.objects.create_user(
            nombre_completo='Admin ParkShare Uno',
            correo='admin@parkshare.mx',
            telefono='5500000001',
            password=self.password,
            fecha_nacimiento=date(1984, 7, 7),
            rol=UserRole.ADMIN,
            estado_cuenta=AccountStatus.ACTIVA,
        )

        # Future schedule window on tomorrow's date so start > now consistently
        tomorrow = (timezone.now().astimezone(MEXICO_TZ) + timedelta(days=1)).date()
        self.future_start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 10, 0, tzinfo=MEXICO_TZ)
        self.future_end = self.future_start + timedelta(minutes=90)

        self.garage = self._create_garage(self.landlord, spaces=1, rate='45.00')
        self.pending_garage = self._create_garage(
            self.landlord, spaces=2, rate='40.00', garage_status=GarageStatus.PENDIENTE
        )

    def _create_garage(
        self,
        landlord: Usuario,
        spaces: int = 1,
        rate: str = '45.00',
        garage_status: str = GarageStatus.APROBADA,
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
            estado_cochera=garage_status,
        )
        FotoCochera.objects.create(cochera=garage, url_foto='https://cdn.parkshare.mx/1.jpg')
        for day in WeekdayChoice.values:
            Disponibilidad.objects.create(
                cochera=garage,
                dia_semana=day,
                hora_inicio=time(0, 0),
                hora_fin=time(23, 59, 59),
            )
        return garage

    def _auth_as(self, email: str) -> None:
        res = self.client.post(
            '/auth/login',
            {'correo': email, 'password': self.password},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    # --- POST /reservas ---

    def test_create_reservation_happy_path_rn01_capacity_and_role_guards(self):
        self._auth_as(self.tenant.correo)
        payload = {
            'cochera_id': self.garage.id_cochera,
            'fecha_hora_inicio': self.future_start.isoformat(),
            'fecha_hora_fin': self.future_end.isoformat(),
        }
        res = self.client.post('/reservas', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['estado'], ReservationStatus.SOLICITADA)
        self.assertEqual(res.data['monto_base'], '67.50')
        self.assertEqual(res.data['monto_penalizacion'], '0.00')
        self.assertEqual(res.data['monto_total'], '67.50')

        # Overlapping slot fails with 409 Conflict (1/1 capacity full)
        self._auth_as(self.other_tenant.correo)
        overlap_payload = {
            'cochera_id': self.garage.id_cochera,
            'fecha_hora_inicio': (self.future_start + timedelta(minutes=30)).isoformat(),
            'fecha_hora_fin': (self.future_end + timedelta(minutes=30)).isoformat(),
        }
        self.assertEqual(
            self.client.post('/reservas', overlap_payload, format='json').status_code,
            status.HTTP_409_CONFLICT,
        )

        # Contiguous/later slot (e.g., User A 13:30-14:30 -> User B 14:30-15:00) ALSO fails with 409
        # while User A's reservation has not been vacated or cancelled
        contiguous_payload = {
            'cochera_id': self.garage.id_cochera,
            'fecha_hora_inicio': self.future_end.isoformat(),
            'fecha_hora_fin': (self.future_end + timedelta(minutes=30)).isoformat(),
        }
        res_v1_blocked = self.client.post('/api/v1/reservas', contiguous_payload, format='json')
        self.assertEqual(res_v1_blocked.status_code, status.HTTP_409_CONFLICT)

        # Once User A cancels, the space is freed and User B can reserve via /api/v1/reservas
        self._auth_as(self.tenant.correo)
        cancel_res = self.client.post(f'/reservas/{res.data["id_reserva"]}/cancelar', {}, format='json')
        self.assertEqual(cancel_res.status_code, status.HTTP_200_OK)

        self._auth_as(self.other_tenant.correo)
        res_v1 = self.client.post('/api/v1/reservas', contiguous_payload, format='json')
        self.assertEqual(res_v1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_v1.data['monto_base'], '22.50')
        self.client.post(f'/reservas/{res_v1.data["id_reserva"]}/cancelar', {}, format='json')

        # RN-01 invalid durations -> 400
        self._auth_as(self.tenant.correo)
        for bad_end in (
            self.future_start + timedelta(minutes=15),
            self.future_start + timedelta(minutes=45),
            self.future_start + timedelta(hours=24, minutes=30),
        ):
            bad_res = self.client.post(
                '/reservas',
                {
                    'cochera_id': self.garage.id_cochera,
                    'fecha_hora_inicio': self.future_start.isoformat(),
                    'fecha_hora_fin': bad_end.isoformat(),
                },
                format='json',
            )
            self.assertEqual(bad_res.status_code, status.HTTP_400_BAD_REQUEST)

        # Non-approved garage -> 409
        res_pending = self.client.post(
            '/reservas',
            {
                'cochera_id': self.pending_garage.id_cochera,
                'fecha_hora_inicio': self.future_start.isoformat(),
                'fecha_hora_fin': self.future_end.isoformat(),
            },
            format='json',
        )
        self.assertEqual(res_pending.status_code, status.HTTP_409_CONFLICT)

        # Landlord cannot reserve -> 403
        self._auth_as(self.landlord.correo)
        self.assertEqual(
            self.client.post('/reservas', payload, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    # --- GET /reservas & GET /reservas/{id} ---

    def test_list_and_detail_reservations_by_role_with_live_metrics(self):
        now_dt = timezone.now()
        # Create one confirmed upcoming, one currently EN_USO ending in 20m, and one finalized
        r_confirmed = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.future_start,
            fecha_hora_fin=self.future_end,
            monto_base=Decimal('67.50'),
            estado=ReservationStatus.CONFIRMADA,
        )
        r_in_use = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=now_dt - timedelta(minutes=40),
            fecha_hora_fin=now_dt + timedelta(minutes=20),
            monto_base=Decimal('45.00'),
            estado=ReservationStatus.EN_USO,
        )
        r_past = Reserva.objects.create(
            arrendatario=self.other_tenant,
            cochera=self.garage,
            fecha_hora_inicio=now_dt - timedelta(hours=3),
            fecha_hora_fin=now_dt - timedelta(hours=2),
            monto_base=Decimal('45.00'),
            estado=ReservationStatus.FINALIZADA,
        )

        # Tenant views with assertNumQueries(2)
        self._auth_as(self.tenant.correo)
        with self.assertNumQueries(2):
            res_upcoming = self.client.get('/reservas?vista=proximas')
        self.assertEqual(res_upcoming.status_code, status.HTTP_200_OK)
        self.assertEqual(res_upcoming.data['count'], 2)

        # Incompatible vista for tenant -> 400
        self.assertEqual(
            self.client.get('/reservas?vista=en_uso').status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        # Detail for EN_USO returns live timer
        res_detail = self.client.get(f'/reservas/{r_in_use.id_reserva}')
        self.assertEqual(res_detail.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res_detail.data['tiempo_en_uso'])
        self.assertGreater(res_detail.data['tiempo_en_uso']['tiempo_restante_minutos'], 0)
        self.assertEqual(res_detail.data['desglose_montos']['recargo_estimado_actual'], '0.00')

        # Third-party tenant gets 403 on detail
        self._auth_as(self.other_tenant.correo)
        self.assertEqual(
            self.client.get(f'/reservas/{r_confirmed.id_reserva}').status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Landlord views: proximas_llegadas, en_uso, historial
        self._auth_as(self.landlord.correo)
        res_arrivals = self.client.get('/reservas?vista=proximas_llegadas')
        self.assertEqual([x['id_reserva'] for x in res_arrivals.data['results']], [r_confirmed.id_reserva])

        res_in_use = self.client.get('/reservas?vista=en_uso')
        self.assertEqual([x['id_reserva'] for x in res_in_use.data['results']], [r_in_use.id_reserva])

        res_hist = self.client.get('/reservas?vista=historial')
        self.assertEqual([x['id_reserva'] for x in res_hist.data['results']], [r_past.id_reserva])

    # --- POST /reservas/{id}/cancelar & PATCH /reservas/{id} ---

    def test_cancel_rn04_rn05_and_patch_state_machine_transitions(self):
        now_dt = timezone.now()
        # 1. Free cancellation <= 10 min on CONFIRMADA (RN-04)
        r_free = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.future_start,
            fecha_hora_fin=self.future_end,
            monto_base=Decimal('67.50'),
            estado=ReservationStatus.CONFIRMADA,
        )
        self._auth_as(self.tenant.correo)
        res_free = self.client.post(f'/reservas/{r_free.id_reserva}/cancelar')
        self.assertEqual(res_free.status_code, status.HTTP_200_OK)
        self.assertEqual(res_free.data['monto_penalizacion'], '0.00')
        self.assertFalse(res_free.data['aplico_multa_tardia'])

        # 2. Late cancellation > 10 min on CONFIRMADA -> $100.00 (RN-05)
        r_late_cancel = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.future_start,
            fecha_hora_fin=self.future_end,
            monto_base=Decimal('67.50'),
            estado=ReservationStatus.CONFIRMADA,
        )
        Reserva.objects.filter(pk=r_late_cancel.id_reserva).update(
            fecha_creacion=now_dt - timedelta(minutes=15)
        )
        res_late = self.client.post(f'/reservas/{r_late_cancel.id_reserva}/cancelar')
        self.assertEqual(res_late.status_code, status.HTTP_200_OK)
        self.assertEqual(res_late.data['monto_penalizacion'], '100.00')
        self.assertTrue(res_late.data['aplico_multa_tardia'])

        # Double cancel returns 409 Conflict
        self.assertEqual(
            self.client.post(f'/reservas/{r_late_cancel.id_reserva}/cancelar').status_code,
            status.HTTP_409_CONFLICT,
        )

        # 3. Full flow SOLICITADA -> CONFIRMADA (admin) -> EN_USO (landlord) -> FINALIZADA with RN-03
        r_flow = self.client.post(
            '/reservas',
            {
                'cochera_id': self.garage.id_cochera,
                'fecha_hora_inicio': self.future_start.isoformat(),
                'fecha_hora_fin': self.future_end.isoformat(),
            },
            format='json',
        ).data['id_reserva']

        # Tenant cannot confirm -> 403
        self.assertEqual(
            self.client.patch(
                f'/reservas/{r_flow}',
                {'estado': 'CONFIRMADA'},
                format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Admin confirms -> 200
        self._auth_as(self.admin.correo)
        self.assertEqual(
            self.client.patch(
                f'/reservas/{r_flow}',
                {'estado': 'CONFIRMADA'},
                format='json',
            ).status_code,
            status.HTTP_200_OK,
        )

        # Landlord starts usage -> 200
        self._auth_as(self.landlord.correo)
        self.assertEqual(
            self.client.patch(
                f'/reservas/{r_flow}',
                {'estado': 'EN_USO'},
                format='json',
            ).status_code,
            status.HTTP_200_OK,
        )

        # Tenant cannot cancel EN_USO -> 409
        self._auth_as(self.tenant.correo)
        self.assertEqual(
            self.client.post(f'/reservas/{r_flow}/cancelar').status_code,
            status.HTTP_409_CONFLICT,
        )

        # Landlord finishes 20m after future_end -> 10m tolerance + 10m late = $100.00
        self._auth_as(self.landlord.correo)
        res_finish = self.client.patch(
            f'/reservas/{r_flow}',
            {
                'estado': 'FINALIZADA',
                'hora_salida_real': (self.future_end + timedelta(minutes=20)).isoformat(),
            },
            format='json',
        )
        self.assertEqual(res_finish.status_code, status.HTTP_200_OK)
        self.assertEqual(res_finish.data['estado'], ReservationStatus.FINALIZADA)
        self.assertEqual(res_finish.data['monto_penalizacion'], '100.00')
        self.assertEqual(res_finish.data['monto_total'], '167.50')

    # --- Integration with GET /cocheras ---

    def test_active_reservation_reduces_realtime_spaces_in_cocheras_search(self):
        multi_garage = self._create_garage(self.landlord, spaces=50, rate='50.00')
        self._auth_as(self.tenant.correo)

        # Before reserving: 50 available
        before_search = self.client.get('/cocheras?lat=19.37124500&lng=-99.17834200')
        by_id_before = {item['id_cochera']: item for item in before_search.data['results']}
        self.assertEqual(by_id_before[multi_garage.id_cochera]['espacios_disponibles_ahora'], 50)

        # Create reservation for a FUTURE window -> immediately decrements 50 -> 49 at booking time
        create_res = self.client.post(
            '/reservas',
            {
                'cochera_id': multi_garage.id_cochera,
                'fecha_hora_inicio': self.future_start.isoformat(),
                'fecha_hora_fin': self.future_end.isoformat(),
            },
            format='json',
        )
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
        rsv_id = create_res.data['id_reserva']

        with self.assertNumQueries(4):
            res_search = self.client.get('/cocheras?lat=19.37124500&lng=-99.17834200')

        self.assertEqual(res_search.status_code, status.HTTP_200_OK)
        by_id = {item['id_cochera']: item for item in res_search.data['results']}
        self.assertEqual(by_id[multi_garage.id_cochera]['espacios_disponibles_ahora'], 49)
        self.assertEqual(by_id[multi_garage.id_cochera]['espacios_ocupados_ahora'], 1)

        # Cancel reservation -> immediately increments 49 -> 50
        self.client.post(f'/reservas/{rsv_id}/cancelar', {}, format='json')
        after_cancel_detail = self.client.get(f'/cocheras/{multi_garage.id_cochera}')
        self.assertEqual(after_cancel_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(after_cancel_detail.data['espacios_disponibles_ahora'], 50)
        self.assertEqual(after_cancel_detail.data['espacios_ocupados_ahora'], 0)

    def test_rn08_single_active_reservation_api(self):
        self._auth_as(self.tenant.correo)
        payload1 = {
            'cochera_id': self.garage.id_cochera,
            'fecha_hora_inicio': self.future_start.isoformat(),
            'fecha_hora_fin': self.future_end.isoformat(),
        }
        res1 = self.client.post('/reservas', payload1, format='json')
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        res_id = res1.data['id_reserva']

        # Attempt to create second reservation while first is SOLICITADA -> 409 Conflict
        payload2 = {
            'cochera_id': self.garage.id_cochera,
            'fecha_hora_inicio': (self.future_end + timedelta(hours=1)).isoformat(),
            'fecha_hora_fin': (self.future_end + timedelta(hours=2)).isoformat(),
        }
        res2 = self.client.post('/reservas', payload2, format='json')
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res2.data['error'], 'ActiveReservationExists')
        self.assertIn('Ya cuentas con una reservación activa', res2.data['detail'])

        # Cancel first reservation -> now can create second reservation
        res_cancel = self.client.post(f'/reservas/{res_id}/cancelar')
        self.assertEqual(res_cancel.status_code, status.HTTP_200_OK)
        self.assertEqual(res_cancel.data['estado'], ReservationStatus.CANCELADA)

        res3 = self.client.post('/reservas', payload2, format='json')
        self.assertEqual(res3.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res3.data['estado'], ReservationStatus.SOLICITADA)

    def test_rn07_patch_tolerance_and_late_auto_cancellation_api(self):
        now_dt = timezone.now()
        # Case 1: Entry exactly at boundary (+20m00s) -> 200 OK, transitions to EN_USO
        r1 = Reserva.objects.create(
            arrendatario=self.other_tenant,
            cochera=self.garage,
            fecha_hora_inicio=now_dt + timedelta(minutes=10),
            fecha_hora_fin=now_dt + timedelta(hours=2),
            monto_base=Decimal('80.00'),
            estado=ReservationStatus.CONFIRMADA,
        )
        self._auth_as(self.landlord.correo)
        with patch('django.utils.timezone.now', return_value=r1.fecha_hora_inicio + timedelta(minutes=20)):
            res1 = self.client.patch(f'/reservas/{r1.id_reserva}', {'estado': 'EN_USO'}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(res1.data['estado'], ReservationStatus.EN_USO)

        # Case 2: Entry 1 second late (+20m01s) -> 409 Conflict with LateEntryCancelled payload
        r2 = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=now_dt + timedelta(hours=3),
            fecha_hora_fin=now_dt + timedelta(hours=5),
            monto_base=Decimal('80.00'),
            estado=ReservationStatus.CONFIRMADA,
        )
        late_now = r2.fecha_hora_inicio + timedelta(minutes=20, seconds=1)
        with patch('django.utils.timezone.now', return_value=late_now):
            res2 = self.client.patch(f'/reservas/{r2.id_reserva}', {'estado': 'EN_USO'}, format='json')
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res2.data['error'], 'LateEntryCancelled')
        self.assertEqual(res2.data['estado_reserva'], ReservationStatus.CANCELADA)
        self.assertEqual(res2.data['monto_penalizacion'], '100.00')
        self.assertIn('Se excedió la tolerancia de 20 minutos para la entrada', res2.data['detail'])

        # DB persistence check
        r2.refresh_from_db()
        self.assertEqual(r2.estado, ReservationStatus.CANCELADA)
        self.assertEqual(r2.monto_penalizacion, Decimal('100.00'))

    def test_multa_acumulada_exposed_in_list_detail_and_cancel_past_entry_tolerance(self):
        now_dt = timezone.now()

        # 1. EN_USO reservation 15m past end_dt (5m beyond 10m RN-02 grace -> $50.00 live surcharge)
        r_overtime = Reserva.objects.create(
            arrendatario=self.other_tenant,
            cochera=self.garage,
            fecha_hora_inicio=now_dt - timedelta(minutes=75),
            fecha_hora_fin=now_dt - timedelta(minutes=15),
            monto_base=Decimal('45.00'),
            estado=ReservationStatus.EN_USO,
        )
        self._auth_as(self.other_tenant.correo)
        with patch('django.utils.timezone.now', return_value=now_dt):
            res_overtime_detail = self.client.get(f'/reservas/{r_overtime.id_reserva}')
            res_overtime_list = self.client.get('/reservas?vista=proximas')
        self.assertEqual(res_overtime_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(res_overtime_detail.data['multa_acumulada'], '50.00')
        self.assertEqual(res_overtime_detail.data['desglose_montos']['multa_acumulada'], '50.00')
        self.assertEqual(res_overtime_detail.data['desglose_montos']['recargo_estimado_actual'], '50.00')
        self.assertEqual(res_overtime_detail.data['desglose_montos']['monto_total_estimado'], '95.00')

        self.assertEqual(res_overtime_list.status_code, status.HTTP_200_OK)
        self.assertEqual(res_overtime_list.data['results'][0]['multa_acumulada'], '50.00')

        # 2. CONFIRMADA reservation 25m past start_dt (> 20m RN-07 tolerance), even if created recently
        r_late_entry = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=now_dt - timedelta(minutes=25),
            fecha_hora_fin=now_dt + timedelta(minutes=95),
            monto_base=Decimal('80.00'),
            estado=ReservationStatus.CONFIRMADA,
        )
        self._auth_as(self.tenant.correo)
        res_pre_detail = self.client.get(f'/reservas/{r_late_entry.id_reserva}')
        self.assertEqual(res_pre_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(res_pre_detail.data['multa_acumulada'], '100.00')
        self.assertEqual(res_pre_detail.data['desglose_montos']['multa_acumulada'], '100.00')

        # Cancelling past the 20m entry tolerance MUST apply $100.00 penalty regardless of fecha_creacion
        res_cancel = self.client.post(f'/reservas/{r_late_entry.id_reserva}/cancelar')
        self.assertEqual(res_cancel.status_code, status.HTTP_200_OK)
        self.assertTrue(res_cancel.data['aplico_multa_tardia'])
        self.assertEqual(res_cancel.data['monto_penalizacion'], '100.00')
        self.assertEqual(res_cancel.data['multa_acumulada'], '100.00')
        self.assertEqual(res_cancel.data['monto_total'], '100.00')

        # Cancelled reservation in history and detail reports $100.00 penalty and $100.00 total (not base + penalty)
        res_hist = self.client.get('/reservas?vista=anteriores')
        self.assertEqual(res_hist.status_code, status.HTTP_200_OK)
        cancelled_item = next(
            x for x in res_hist.data['results'] if x['id_reserva'] == r_late_entry.id_reserva
        )
        self.assertEqual(cancelled_item['monto_penalizacion'], '100.00')
        self.assertEqual(cancelled_item['multa_acumulada'], '100.00')
        self.assertEqual(cancelled_item['monto_total'], '100.00')


