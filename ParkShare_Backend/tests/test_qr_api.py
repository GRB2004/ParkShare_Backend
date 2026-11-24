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
    CodigoQR,
    Disponibilidad,
    GarageStatus,
    QRCodeType,
    Reserva,
    ReservationStatus,
    UserRole,
    VehicleType,
    WeekdayChoice,
)
from ParkShare_Backend.services.reserva_service import confirm_reservation, create_reservation

Usuario = get_user_model()
MEXICO_TZ = ZoneInfo('America/Mexico_City')


class QRAPITests(APITestCase):
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
        self.pending_landlord = Usuario.objects.create_user(
            nombre_completo='Pedro Pendiente López',
            correo='pedro.pendiente@gmail.com',
            telefono='5512340000',
            password=self.password,
            fecha_nacimiento=date(1990, 1, 10),
            rol=UserRole.ARRENDADOR,
            estado_cuenta=AccountStatus.PENDIENTE,
            cuenta_cobro='012180000000000001',
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
            modelo_vehiculo='Volkswagen Jetta 2020',
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

        self.garage = Cochera.objects.create(
            arrendador=self.landlord,
            nombre='Cochera Los Pinos',
            direccion='Av. Insurgentes Sur 1450',
            latitud=Decimal('19.37124500'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('35.50'),
            espacios_totales=2,
            tarifa_hora=Decimal('40.00'),
            tipo_vehiculo=VehicleType.AMBOS,
            estado_cochera=GarageStatus.APROBADA,
        )
        for day in WeekdayChoice.values:
            Disponibilidad.objects.create(
                cochera=self.garage,
                dia_semana=day,
                hora_inicio=time(0, 0),
                hora_fin=time(23, 59, 59),
            )

        base_now = timezone.now().astimezone(MEXICO_TZ).replace(second=0, microsecond=0)
        self.start_dt = base_now + timedelta(hours=2)
        self.end_dt = self.start_dt + timedelta(hours=2)

    def _auth_as(self, email: str) -> None:
        res = self.client.post(
            '/auth/login',
            {'correo': email, 'password': self.password},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    # --- GET /reservas/{id}/qr ---

    def test_get_reservation_qr_states_permissions_and_num_queries(self):
        reservation = create_reservation(
            self.tenant,
            self.garage.id_cochera,
            self.start_dt,
            self.end_dt,
            now=self.start_dt - timedelta(hours=2),
        )

        # Unauthenticated -> 401
        self.client.credentials()
        self.assertEqual(
            self.client.get(f'/reservas/{reservation.id_reserva}/qr').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        # SOLICITADA -> 409 Conflict
        self._auth_as(self.tenant.correo)
        self.assertEqual(
            self.client.get(f'/reservas/{reservation.id_reserva}/qr').status_code,
            status.HTTP_409_CONFLICT,
        )

        # Missing reservation -> 404
        self.assertEqual(
            self.client.get('/reservas/999999/qr').status_code,
            status.HTTP_404_NOT_FOUND,
        )

        # Confirm reservation -> auto-issues ENTRADA QR
        confirm_reservation(reservation.id_reserva, by_system=True, now=self.start_dt - timedelta(hours=1))

        # Owning tenant retrieves ENTRADA QR in 3 constant queries (auth + reserva + prefetch codigos_qr)
        with self.assertNumQueries(3):
            res_entry = self.client.get(f'/reservas/{reservation.id_reserva}/qr')
        self.assertEqual(res_entry.status_code, status.HTTP_200_OK)
        self.assertEqual(res_entry.data['tipo_qr'], QRCodeType.ENTRADA)
        self.assertFalse(res_entry.data['usado'])
        self.assertEqual(res_entry.data['reserva']['estado'], ReservationStatus.CONFIRMADA)
        self.assertEqual(res_entry.data['reserva']['monto_base'], '80.00')
        self.assertEqual(res_entry.data['reserva']['cochera']['id_cochera'], self.garage.id_cochera)
        self.assertEqual(res_entry.data['reserva']['cochera']['nombre'], self.garage.nombre)
        self.assertEqual(res_entry.data['reserva']['cochera']['direccion'], self.garage.direccion)
        self.assertIsInstance(res_entry.data['reserva']['cochera']['latitud'], float)
        self.assertIsInstance(res_entry.data['reserva']['cochera']['longitud'], float)
        self.assertEqual(res_entry.data['reserva']['arrendatario']['id_usuario'], self.tenant.id_usuario)
        self.assertEqual(res_entry.data['reserva']['arrendatario']['placas'], 'ABC-123-D')
        self.assertEqual(
            res_entry.data['reserva']['arrendatario']['modelo_vehiculo'],
            'Volkswagen Jetta 2020',
        )

        # /api/v1/ mirror route works
        self.assertEqual(
            self.client.get(f'/api/v1/reservas/{reservation.id_reserva}/qr').status_code,
            status.HTTP_200_OK,
        )

        # Cross-tenant and landlord get 403 Forbidden
        for email in (self.other_tenant.correo, self.landlord.correo):
            self._auth_as(email)
            self.assertEqual(
                self.client.get(f'/reservas/{reservation.id_reserva}/qr').status_code,
                status.HTTP_403_FORBIDDEN,
            )

    # --- POST /reservas/validar ---

    def test_validate_qr_full_lifecycle_entry_and_checkout_rn02_rn03_rn06(self):
        created_at = self.start_dt - timedelta(hours=2)
        reservation = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=created_at
        )
        confirm_reservation(reservation.id_reserva, by_system=True, now=created_at)

        self._auth_as(self.tenant.correo)
        entry_token = self.client.get(f'/reservas/{reservation.id_reserva}/qr').data['token_qr']

        # Tenant cannot call /reservas/validar -> 403
        self.assertEqual(
            self.client.post('/reservas/validar', {'token': entry_token}, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Pending landlord blocked by RN-08 -> 403
        self._auth_as(self.pending_landlord.correo)
        self.assertEqual(
            self.client.post('/reservas/validar', {'token': entry_token}, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Other landlord cannot validate garage they don't own -> 403
        self._auth_as(self.other_landlord.correo)
        self.assertEqual(
            self.client.post('/reservas/validar', {'token': entry_token}, format='json').status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Too early arrival (2h before start) -> 409 without consuming QR
        self._auth_as(self.landlord.correo)
        with patch('django.utils.timezone.now', return_value=self.start_dt - timedelta(hours=2)):
            res_early = self.client.post('/reservas/validar', {'token': entry_token}, format='json')
        self.assertEqual(res_early.status_code, status.HTTP_409_CONFLICT)

        # Valid arrival 10 min before start -> 200 OK, marks EN_USO, creates SALIDA QR without leaking token
        with patch('django.utils.timezone.now', return_value=self.start_dt - timedelta(minutes=10)):
            res_enter = self.client.post('/reservas/validar', {'token': entry_token}, format='json')
        self.assertEqual(res_enter.status_code, status.HTTP_200_OK)
        self.assertEqual(res_enter.data['tipo_qr'], QRCodeType.ENTRADA)
        self.assertEqual(res_enter.data['estado_reserva'], ReservationStatus.EN_USO)
        self.assertTrue(res_enter.data['qr_salida_emitido'])
        self.assertNotIn('token_qr', res_enter.data)
        self.assertEqual(res_enter.data['verificacion']['arrendatario']['placas'], 'ABC-123-D')

        # Re-scanning already used ENTRADA token -> 409 Conflict
        with patch('django.utils.timezone.now', return_value=self.start_dt):
            res_reuse = self.client.post('/reservas/validar', {'token': entry_token}, format='json')
        self.assertEqual(res_reuse.status_code, status.HTTP_409_CONFLICT)

        # Tenant retrieves SALIDA QR via GET /reservas/{id}/qr
        self._auth_as(self.tenant.correo)
        res_exit_get = self.client.get(f'/reservas/{reservation.id_reserva}/qr')
        self.assertEqual(res_exit_get.status_code, status.HTTP_200_OK)
        self.assertEqual(res_exit_get.data['tipo_qr'], QRCodeType.SALIDA)
        exit_token = res_exit_get.data['token_qr']

        # Host validates SALIDA 20 minutes after end_dt -> 10m tolerance + 10m late = $100.00 (RN-03)
        self._auth_as(self.landlord.correo)
        with patch('django.utils.timezone.now', return_value=self.end_dt + timedelta(minutes=20)):
            res_exit_scan = self.client.post(
                '/api/v1/reservas/validar', {'token': exit_token}, format='json'
            )
        self.assertEqual(res_exit_scan.status_code, status.HTTP_200_OK)
        self.assertEqual(res_exit_scan.data['tipo_qr'], QRCodeType.SALIDA)
        self.assertEqual(res_exit_scan.data['estado_reserva'], ReservationStatus.FINALIZADA)
        self.assertEqual(res_exit_scan.data['verificacion']['tolerancia_minutos'], 10)
        self.assertEqual(res_exit_scan.data['verificacion']['minutos_retraso'], 10)
        self.assertEqual(res_exit_scan.data['verificacion']['monto_base'], '80.00')
        self.assertEqual(res_exit_scan.data['verificacion']['monto_penalizacion'], '100.00')
        self.assertEqual(res_exit_scan.data['verificacion']['monto_total'], '180.00')

        # Once FINALIZADA, tenant gets 409 on GET /reservas/{id}/qr
        self._auth_as(self.tenant.correo)
        self.assertEqual(
            self.client.get(f'/reservas/{reservation.id_reserva}/qr').status_code,
            status.HTTP_409_CONFLICT,
        )

    def test_validate_qr_bad_format_not_found_and_expired_410(self):
        created_at = self.start_dt - timedelta(hours=2)
        reservation = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=created_at
        )
        confirm_reservation(reservation.id_reserva, by_system=True, now=created_at)
        entry_token = CodigoQR.objects.get(reserva=reservation, tipo_qr=QRCodeType.ENTRADA).token_qr

        # Anonymous -> 401
        self.client.credentials()
        self.assertEqual(
            self.client.post('/reservas/validar', {'token': entry_token}, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        self._auth_as(self.landlord.correo)
        for bad_payload in ({}, {'token': ''}, {'token': 'xyz'}, {'token': 'a' * 63}):
            self.assertEqual(
                self.client.post('/reservas/validar', bad_payload, format='json').status_code,
                status.HTTP_400_BAD_REQUEST,
            )

        self.assertEqual(
            self.client.post('/reservas/validar', {'token': '0' * 64}, format='json').status_code,
            status.HTTP_404_NOT_FOUND,
        )

        # Expired QR -> 410 Gone
        with patch('django.utils.timezone.now', return_value=self.end_dt + timedelta(minutes=1)):
            res_gone = self.client.post('/reservas/validar', {'token': entry_token}, format='json')
        self.assertEqual(res_gone.status_code, status.HTTP_410_GONE)
        self.assertFalse(
            CodigoQR.objects.get(reserva=reservation, tipo_qr=QRCodeType.ENTRADA).usado
        )

    def test_rn07_validate_entry_qr_tolerance_and_late_auto_cancellation_api(self):
        created_at = self.start_dt - timedelta(hours=2)
        # 1. Entry QR scanned at boundary (+20m00s) -> 200 OK
        r1 = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=created_at
        )
        confirm_reservation(r1.id_reserva, by_system=True, now=created_at)
        qr1 = CodigoQR.objects.get(reserva=r1, tipo_qr=QRCodeType.ENTRADA)

        self._auth_as(self.landlord.correo)
        with patch('django.utils.timezone.now', return_value=self.start_dt + timedelta(minutes=20)):
            res1 = self.client.post('/reservas/validar', {'token': qr1.token_qr}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(res1.data['tipo_qr'], QRCodeType.ENTRADA)
        self.assertEqual(res1.data['estado_reserva'], ReservationStatus.EN_USO)
        self.assertTrue(res1.data['qr_salida_emitido'])

        # 2. Entry QR scanned 1 second late (+20m01s) -> 409 Conflict with LateEntryCancelled payload
        r2_start = self.start_dt + timedelta(hours=4)
        r2_end = self.end_dt + timedelta(hours=4)
        r2 = create_reservation(
            self.other_tenant, self.garage.id_cochera, r2_start, r2_end, now=created_at
        )
        confirm_reservation(r2.id_reserva, by_system=True, now=created_at)
        qr2 = CodigoQR.objects.get(reserva=r2, tipo_qr=QRCodeType.ENTRADA)

        with patch('django.utils.timezone.now', return_value=r2_start + timedelta(minutes=20, seconds=1)):
            res2 = self.client.post('/reservas/validar', {'token': qr2.token_qr}, format='json')
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res2.data['error'], 'LateEntryCancelled')
        self.assertEqual(res2.data['estado_reserva'], ReservationStatus.CANCELADA)
        self.assertEqual(res2.data['monto_penalizacion'], '100.00')
        self.assertIn('Se excedió la tolerancia de 20 minutos para la entrada', res2.data['detail'])

        # DB persistence check
        r2.refresh_from_db()
        self.assertEqual(r2.estado, ReservationStatus.CANCELADA)
        self.assertEqual(r2.monto_penalizacion, Decimal('100.00'))

        qr2.refresh_from_db()
        self.assertFalse(qr2.usado)
        self.assertFalse(
            CodigoQR.objects.filter(reserva=r2, tipo_qr=QRCodeType.SALIDA).exists()
        )

    def test_simular_qr_list_and_post_simulation_lifecycle_api(self):
        created_at = self.start_dt - timedelta(hours=2)
        r1 = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=created_at
        )
        confirm_reservation(r1.id_reserva, by_system=True, now=created_at)

        # Unauthenticated -> 401
        self.client.credentials()
        self.assertEqual(self.client.get('/reservas/simular-qr').status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            self.client.post('/reservas/simular-qr', {}, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        # Tenant and pending landlord -> 403
        for forbidden_email in (self.tenant.correo, self.pending_landlord.correo):
            self._auth_as(forbidden_email)
            self.assertEqual(self.client.get('/reservas/simular-qr').status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(
                self.client.post('/reservas/simular-qr', {}, format='json').status_code,
                status.HTTP_403_FORBIDDEN,
            )

        # Active landlord lists active QRs for simulation in 3 constant queries (auth + count + select_related list)
        self._auth_as(self.landlord.correo)
        with patch('django.utils.timezone.now', return_value=created_at):
            with self.assertNumQueries(3):
                res_list = self.client.get('/reservas/simular-qr')
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertEqual(res_list.data['count'], 1)
        item = res_list.data['results'][0]
        self.assertEqual(item['reserva_id'], r1.id_reserva)
        self.assertEqual(item['tipo_qr'], QRCodeType.ENTRADA)
        self.assertEqual(item['cochera_nombre'], self.garage.nombre)
        self.assertEqual(item['placas'], 'ABC-123-D')
        self.assertEqual(item['modelo_vehiculo'], 'Volkswagen Jetta 2020')
        self.assertEqual(item['nombre_arrendatario'], self.tenant.nombre_completo)
        self.assertEqual(item['estado_reserva'], ReservationStatus.CONFIRMADA)

        # Other landlord sees 0 active QRs and gets 403 if trying to simulate r1
        self._auth_as(self.other_landlord.correo)
        with patch('django.utils.timezone.now', return_value=created_at):
            res_other_list = self.client.get('/reservas/simular-qr')
            res_other_post = self.client.post(
                '/reservas/simular-qr', {'reserva_id': r1.id_reserva}, format='json'
            )
        self.assertEqual(res_other_list.data['count'], 0)
        self.assertEqual(res_other_post.status_code, status.HTTP_403_FORBIDDEN)

        self._auth_as(self.landlord.correo)

        # Invalid tipo_qr query param -> 400
        self.assertEqual(
            self.client.get('/reservas/simular-qr?tipo_qr=OTRO').status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        # respetar_ventana=True on future reservation (2h before start) -> 409 Conflict
        with patch('django.utils.timezone.now', return_value=created_at):
            res_strict = self.client.post(
                '/reservas/simular-qr',
                {'reserva_id': r1.id_reserva, 'respetar_ventana': True},
                format='json',
            )
        self.assertEqual(res_strict.status_code, status.HTTP_409_CONFLICT)

        # Mismatched token + reserva_id -> 409 Conflict
        with patch('django.utils.timezone.now', return_value=created_at):
            res_mismatch = self.client.post(
                '/reservas/simular-qr',
                {'token': item['token_qr'], 'reserva_id': r1.id_reserva + 999},
                format='json',
            )
        self.assertEqual(res_mismatch.status_code, status.HTTP_409_CONFLICT)

        # Simulate ENTRADA on future reservation (respetar_ventana=False default)
        with patch('django.utils.timezone.now', return_value=created_at):
            res_sim_in = self.client.post(
                '/reservas/simular-qr', {'tipo_qr': 'ENTRADA'}, format='json'
            )
        self.assertEqual(res_sim_in.status_code, status.HTTP_200_OK)
        self.assertTrue(res_sim_in.data['simulado'])
        self.assertEqual(res_sim_in.data['tipo_qr'], QRCodeType.ENTRADA)
        self.assertEqual(res_sim_in.data['estado_reserva'], ReservationStatus.EN_USO)
        self.assertTrue(res_sim_in.data['qr_salida_emitido'])

        # Now GET /api/v1/reservas/simular-qr returns the SALIDA QR
        with patch('django.utils.timezone.now', return_value=created_at):
            res_list_out = self.client.get('/api/v1/reservas/simular-qr?tipo_qr=SALIDA')
        self.assertEqual(res_list_out.status_code, status.HTTP_200_OK)
        self.assertEqual(res_list_out.data['count'], 1)
        self.assertEqual(res_list_out.data['results'][0]['tipo_qr'], QRCodeType.SALIDA)

        # Simulate SALIDA via /api/v1/reservas/simular-qr with empty body {} -> auto-picks r1 SALIDA
        with patch('django.utils.timezone.now', return_value=created_at):
            res_sim_out = self.client.post('/api/v1/reservas/simular-qr', {}, format='json')
        self.assertEqual(res_sim_out.status_code, status.HTTP_200_OK)
        self.assertTrue(res_sim_out.data['simulado'])
        self.assertEqual(res_sim_out.data['tipo_qr'], QRCodeType.SALIDA)
        self.assertEqual(res_sim_out.data['estado_reserva'], ReservationStatus.FINALIZADA)

        # Re-simulating already used token -> 409 QRAlreadyUsed
        with patch('django.utils.timezone.now', return_value=created_at):
            res_used = self.client.post(
                '/reservas/simular-qr', {'token': item['token_qr']}, format='json'
            )
        self.assertEqual(res_used.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res_used.data['error'], 'QRAlreadyUsed')

        # No active QRs remaining -> 404 NoActiveQRToSimulate
        with patch('django.utils.timezone.now', return_value=created_at):
            res_empty = self.client.post('/reservas/simular-qr', {}, format='json')
        self.assertEqual(res_empty.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res_empty.data['error'], 'NoActiveQRToSimulate')

