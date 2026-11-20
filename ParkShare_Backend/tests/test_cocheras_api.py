from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    Disponibilidad,
    FotoCochera,
    GarageStatus,
    UserRole,
    VehicleType,
    WeekdayChoice,
)

Usuario = get_user_model()


class CocherasAPITests(APITestCase):
    def setUp(self):
        self.password = 'MiPasswordSegura123!'
        self.active_landlord = Usuario.objects.create_user(
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
        self.rejected_landlord = Usuario.objects.create_user(
            nombre_completo='Rosa Rechazada Díaz',
            correo='rosa.rechazada@gmail.com',
            telefono='5512340001',
            password=self.password,
            fecha_nacimiento=date(1991, 2, 20),
            rol=UserRole.ARRENDADOR,
            estado_cuenta=AccountStatus.RECHAZADA,
            cuenta_cobro='012180000000000002',
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
        self.admin = Usuario.objects.create_user(
            nombre_completo='Admin ParkShare Uno',
            correo='admin@parkshare.mx',
            telefono='5500000001',
            password=self.password,
            fecha_nacimiento=date(1984, 7, 7),
            rol=UserRole.ADMIN,
            estado_cuenta=AccountStatus.ACTIVA,
        )

    def _get_token(self, email: str) -> str:
        res = self.client.post(
            '/auth/login',
            {'correo': email, 'password': self.password},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        return res.data['access']

    def _auth_as(self, email: str) -> None:
        token = self._get_token(email)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def _valid_payload(self) -> dict:
        return {
            'nombre': 'Cochera Los Pinos',
            'direccion': 'Av. Insurgentes Sur 1450, Col. Actipan, C.P. 03230',
            'latitud': 19.37124500,
            'longitud': -99.17834200,
            'metros_cuadrados': '35.50',
            'espacios_totales': 2,
            'tarifa_hora': '45.00',
            'tipo_vehiculo': 'AMBOS',
            'fotos': [
                'https://storage.parkshare.mx/cocheras/pinos_1.jpg',
                'https://storage.parkshare.mx/cocheras/pinos_2.webp',
            ],
            'disponibilidades': [
                {'dia_semana': 'L', 'hora_inicio': '07:00', 'hora_fin': '22:00'},
                {'dia_semana': 'M', 'hora_inicio': '07:00', 'hora_fin': '22:00'},
            ],
        }

    def _create_approved_garage(
        self,
        landlord: Usuario,
        name: str,
        lat: str,
        lng: str,
        rate: str,
        spaces: int = 2,
        vehicle_type: str = VehicleType.AMBOS,
    ) -> Cochera:
        garage = Cochera.objects.create(
            arrendador=landlord,
            nombre=name,
            direccion=f'Direccion de {name}',
            latitud=Decimal(lat),
            longitud=Decimal(lng),
            metros_cuadrados=Decimal('30.00'),
            espacios_totales=spaces,
            tarifa_hora=Decimal(rate),
            tipo_vehiculo=vehicle_type,
            estado_cochera=GarageStatus.APROBADA,
        )
        FotoCochera.objects.create(
            cochera=garage,
            url_foto=f'https://storage.parkshare.mx/cocheras/{garage.id_cochera}.jpg',
        )
        for day in WeekdayChoice.values:
            Disponibilidad.objects.create(
                cochera=garage,
                dia_semana=day,
                hora_inicio=time(0, 0),
                hora_fin=time(23, 59, 59),
            )
        return garage

    # --- POST /cocheras ---

    def test_create_garage_happy_path_and_forces_pending(self):
        self._auth_as(self.active_landlord.correo)
        payload = self._valid_payload()
        payload['estado_cochera'] = 'APROBADA'  # Must be ignored

        res = self.client.post('/cocheras', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['estado_cochera'], GarageStatus.PENDIENTE)
        self.assertEqual(res.data['arrendador_id'], self.active_landlord.id_usuario)
        self.assertEqual(len(res.data['fotos']), 2)
        self.assertEqual(len(res.data['disponibilidades']), 2)
        self.assertEqual(res.data['disponibilidades'][0]['hora_inicio'], '07:00:00')

        # Also test /api/v1/ prefix mirror works
        res_v1 = self.client.post('/api/v1/cocheras', self._valid_payload(), format='json')
        self.assertEqual(res_v1.status_code, status.HTTP_201_CREATED)

    def test_create_garage_rn08_blocks_unauthorized_roles_and_inactive_accounts(self):
        for email in (
            self.pending_landlord.correo,
            self.rejected_landlord.correo,
            self.tenant.correo,
        ):
            self._auth_as(email)
            res = self.client.post('/cocheras', self._valid_payload(), format='json')
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        self.client.credentials()
        res_anon = self.client.post('/cocheras', self._valid_payload(), format='json')
        self.assertEqual(res_anon.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_garage_validation_errors_rn09_and_cu03(self):
        self._auth_as(self.active_landlord.correo)

        invalid_cases = [
            {'fotos': []},
            {'fotos': ['https://storage.parkshare.mx/cocheras/doc.pdf']},
            {'metros_cuadrados': '0.00'},
            {'espacios_totales': 0},
            {'tarifa_hora': '-5.00'},
            {'latitud': 95.0},
            {'longitud': -190.0},
            {'disponibilidades': []},
            {'disponibilidades': ['invalido']},
            {'disponibilidades': [{'dia_semana': 123, 'hora_inicio': '08:00', 'hora_fin': '10:00'}]},
            {'disponibilidades': [{'dia_semana': True, 'hora_inicio': '08:00', 'hora_fin': '10:00'}]},
            {'disponibilidades': [{'dia_semana': ['L'], 'hora_inicio': '08:00', 'hora_fin': '10:00'}]},
            {'disponibilidades': [{'dia_semana': 'L', 'hora_inicio': '18:00', 'hora_fin': '09:00'}]},
            {
                'disponibilidades': [
                    {'dia_semana': 'L', 'hora_inicio': '08:00', 'hora_fin': '14:00'},
                    {'dia_semana': 'L', 'hora_inicio': '12:00', 'hora_fin': '18:00'},
                ]
            },
        ]
        for override in invalid_cases:
            payload = self._valid_payload()
            payload.update(override)
            res = self.client.post('/cocheras', payload, format='json')
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, msg=f'Failed for {override}')

    # --- PATCH /admin/cocheras/{id}/validar ---

    def test_admin_validate_garage_approve_reject_and_conflicts(self):
        self._auth_as(self.active_landlord.correo)
        create_res = self.client.post('/cocheras', self._valid_payload(), format='json')
        garage_id = create_res.data['id_cochera']

        # Non-admin forbidden
        res_forbidden = self.client.patch(
            f'/admin/cocheras/{garage_id}/validar',
            {'estado_cochera': 'APROBADA'},
            format='json',
        )
        self.assertEqual(res_forbidden.status_code, status.HTTP_403_FORBIDDEN)

        # Admin approves
        self._auth_as(self.admin.correo)
        res_ok = self.client.patch(
            f'/admin/cocheras/{garage_id}/validar',
            {'estado_cochera': 'APROBADA'},
            format='json',
        )
        self.assertEqual(res_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(res_ok.data['estado_cochera'], 'APROBADA')
        self.assertIsNone(res_ok.data['motivo'])

        # Double validation returns 409 Conflict
        res_conflict = self.client.patch(
            f'/admin/cocheras/{garage_id}/validar',
            {'estado_cochera': 'RECHAZADA', 'motivo': 'Ya aprobada'},
            format='json',
        )
        self.assertEqual(res_conflict.status_code, status.HTTP_409_CONFLICT)

        # Reject without motivo returns 400
        self._auth_as(self.active_landlord.correo)
        g2_id = self.client.post('/cocheras', self._valid_payload(), format='json').data['id_cochera']

        self._auth_as(self.admin.correo)
        res_bad_reject = self.client.patch(
            f'/admin/cocheras/{g2_id}/validar',
            {'estado_cochera': 'RECHAZADA', 'motivo': ''},
            format='json',
        )
        self.assertEqual(res_bad_reject.status_code, status.HTTP_400_BAD_REQUEST)

        res_reject = self.client.patch(
            f'/admin/cocheras/{g2_id}/validar',
            {'estado_cochera': 'RECHAZADA', 'motivo': 'Fotos borrosas.'},
            format='json',
        )
        self.assertEqual(res_reject.status_code, status.HTTP_200_OK)
        self.assertEqual(res_reject.data['estado_cochera'], 'RECHAZADA')
        self.assertEqual(res_reject.data['motivo'], 'Fotos borrosas.')

        res_not_found = self.client.patch(
            '/admin/cocheras/999999/validar',
            {'estado_cochera': 'APROBADA'},
            format='json',
        )
        self.assertEqual(res_not_found.status_code, status.HTTP_404_NOT_FOUND)

    # --- GET /cocheras?lat=&lng=&orden= ---

    def test_search_nearby_garages_filters_sorting_and_no_n_plus_1(self):
        g_near = self._create_approved_garage(
            self.active_landlord,
            'Cochera Cerca Cara',
            '19.42702500',
            '-99.16766500',
            '80.00',
            spaces=2,
            vehicle_type=VehicleType.AUTO,
        )
        g_mid = self._create_approved_garage(
            self.active_landlord,
            'Cochera Media Mas Espacios',
            '19.41944400',
            '-99.16277800',
            '45.00',
            spaces=6,
            vehicle_type=VehicleType.AMBOS,
        )
        g_far = self._create_approved_garage(
            self.active_landlord,
            'Cochera Lejos Barata Moto',
            '19.43260800',
            '-99.13320800',
            '20.00',
            spaces=4,
            vehicle_type=VehicleType.MOTO,
        )
        # Pending garage at exact spot should not appear
        Cochera.objects.create(
            arrendador=self.active_landlord,
            nombre='Cochera Pendiente Oculta',
            direccion='Oculta',
            latitud=Decimal('19.42702500'),
            longitud=Decimal('-99.16766500'),
            metros_cuadrados=Decimal('20.00'),
            espacios_totales=1,
            tarifa_hora=Decimal('10.00'),
            estado_cochera=GarageStatus.PENDIENTE,
        )

        self._auth_as(self.tenant.correo)

        # Missing lat/lng -> 400
        self.assertEqual(self.client.get('/cocheras').status_code, status.HTTP_400_BAD_REQUEST)
        # Invalid lat/lng/orden -> 400
        self.assertEqual(
            self.client.get('/cocheras?lat=99&lng=-99.16').status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.get('/cocheras?lat=19.42&lng=-99.16&orden=invalido').status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        # Proximity sort & constant query count (auth user + count/select + prefetch fotos + prefetch disponibilidades = 4 queries)
        with self.assertNumQueries(4):
            res_near = self.client.get('/cocheras?lat=19.42702500&lng=-99.16766500')

        self.assertEqual(res_near.status_code, status.HTTP_200_OK)
        self.assertEqual(res_near.data['count'], 3)
        self.assertEqual(
            [item['id_cochera'] for item in res_near.data['results']],
            [g_near.id_cochera, g_mid.id_cochera, g_far.id_cochera],
        )
        self.assertEqual(res_near.data['results'][0]['distancia_km'], 0.0)
        self.assertTrue(res_near.data['results'][0]['abierta_ahora'])
        self.assertEqual(res_near.data['results'][0]['espacios_disponibles_ahora'], 2)
        self.assertIsNotNone(res_near.data['results'][0]['foto_principal'])

        # Price sort
        res_price = self.client.get('/cocheras?lat=19.42702500&lng=-99.16766500&orden=precio')
        self.assertEqual(
            [item['id_cochera'] for item in res_price.data['results']],
            [g_far.id_cochera, g_mid.id_cochera, g_near.id_cochera],
        )

        # Availability sort
        res_avail = self.client.get('/cocheras?lat=19.42702500&lng=-99.16766500&orden=disponibilidad')
        self.assertEqual(
            [item['id_cochera'] for item in res_avail.data['results']],
            [g_mid.id_cochera, g_far.id_cochera, g_near.id_cochera],
        )

        # Vehicle type MOTO filter
        res_moto = self.client.get('/cocheras?lat=19.42702500&lng=-99.16766500&tipo_vehiculo=MOTO')
        self.assertEqual(
            {item['id_cochera'] for item in res_moto.data['results']},
            {g_mid.id_cochera, g_far.id_cochera},
        )

        # Vehicle type AMBOS filter
        res_ambos = self.client.get('/cocheras?lat=19.42702500&lng=-99.16766500&tipo_vehiculo=AMBOS')
        self.assertEqual(
            {item['id_cochera'] for item in res_ambos.data['results']},
            {g_mid.id_cochera},
        )

        # Radius filter
        res_radius = self.client.get('/cocheras?lat=19.42702500&lng=-99.16766500&radio_km=1.5')
        self.assertEqual(
            {item['id_cochera'] for item in res_radius.data['results']},
            {g_near.id_cochera, g_mid.id_cochera},
        )

    # --- GET /cocheras/mis-cocheras ---

    def test_landlord_mis_cocheras_endpoint(self):
        self._create_approved_garage(
            self.active_landlord, 'Aprobada Mia', '19.37', '-99.17', '40.00'
        )
        Cochera.objects.create(
            arrendador=self.active_landlord,
            nombre='Pendiente Mia',
            direccion='Calle 2',
            latitud=Decimal('19.38'),
            longitud=Decimal('-99.18'),
            metros_cuadrados=Decimal('20.00'),
            espacios_totales=1,
            tarifa_hora=Decimal('30.00'),
            estado_cochera=GarageStatus.PENDIENTE,
        )
        self._create_approved_garage(
            self.other_landlord, 'Aprobada Otra', '19.39', '-99.19', '50.00'
        )

        self._auth_as(self.tenant.correo)
        self.assertEqual(self.client.get('/cocheras/mis-cocheras').status_code, status.HTTP_403_FORBIDDEN)

        self._auth_as(self.active_landlord.correo)
        res = self.client.get('/cocheras/mis-cocheras')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 2)

        res_filtered = self.client.get('/cocheras/mis-cocheras?estado=PENDIENTE')
        self.assertEqual(res_filtered.data['count'], 1)
        self.assertEqual(res_filtered.data['results'][0]['estado_cochera'], GarageStatus.PENDIENTE)

    # --- GET /cocheras/{id} & PATCH /cocheras/{id} ---

    def test_garage_detail_visibility_and_patch_transitions(self):
        pending_garage = Cochera.objects.create(
            arrendador=self.active_landlord,
            nombre='Cochera Privada Revision',
            direccion='Calle Oculta 1',
            latitud=Decimal('19.37124500'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('35.50'),
            espacios_totales=2,
            tarifa_hora=Decimal('45.00'),
            estado_cochera=GarageStatus.PENDIENTE,
        )
        FotoCochera.objects.create(cochera=pending_garage, url_foto='https://cdn.parkshare.mx/1.jpg')
        Disponibilidad.objects.create(
            cochera=pending_garage,
            dia_semana=WeekdayChoice.L,
            hora_inicio=time(8, 0),
            hora_fin=time(18, 0),
        )

        # Tenant gets 404 for PENDIENTE garage
        self._auth_as(self.tenant.correo)
        self.assertEqual(
            self.client.get(f'/cocheras/{pending_garage.id_cochera}').status_code,
            status.HTTP_404_NOT_FOUND,
        )

        # Owner and admin get 200 with constant queries
        self._auth_as(self.active_landlord.correo)
        with self.assertNumQueries(4):
            res_owner = self.client.get(f'/cocheras/{pending_garage.id_cochera}')
        self.assertEqual(res_owner.status_code, status.HTTP_200_OK)
        self.assertIsNone(res_owner.data['calificacion_promedio'])
        self.assertEqual(res_owner.data['total_resenas'], 0)
        self.assertEqual(res_owner.data['arrendador']['id_usuario'], self.active_landlord.id_usuario)

        # Owner cannot self-approve PENDIENTE -> APROBADA
        res_self_approve = self.client.patch(
            f'/cocheras/{pending_garage.id_cochera}',
            {'estado_cochera': 'APROBADA'},
            format='json',
        )
        self.assertEqual(res_self_approve.status_code, status.HTTP_403_FORBIDDEN)

        # Owner cannot pause PENDIENTE -> INACTIVA
        res_pause_pending = self.client.patch(
            f'/cocheras/{pending_garage.id_cochera}',
            {'estado_cochera': 'INACTIVA'},
            format='json',
        )
        self.assertEqual(res_pause_pending.status_code, status.HTTP_409_CONFLICT)

        # Admin approves
        self._auth_as(self.admin.correo)
        self.assertEqual(
            self.client.patch(
                f'/admin/cocheras/{pending_garage.id_cochera}/validar',
                {'estado_cochera': 'APROBADA'},
                format='json',
            ).status_code,
            status.HTTP_200_OK,
        )

        # Now tenant can view detail
        self._auth_as(self.tenant.correo)
        self.assertEqual(
            self.client.get(f'/cocheras/{pending_garage.id_cochera}').status_code,
            status.HTTP_200_OK,
        )

        # Other landlord cannot PATCH
        self._auth_as(self.other_landlord.correo)
        self.assertEqual(
            self.client.patch(
                f'/cocheras/{pending_garage.id_cochera}',
                {'tarifa_hora': '100.00'},
                format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Owner updates rate, schedule, and pauses APROBADA -> INACTIVA
        self._auth_as(self.active_landlord.correo)
        res_patch = self.client.patch(
            f'/cocheras/{pending_garage.id_cochera}',
            {
                'tarifa_hora': '50.00',
                'estado_cochera': 'INACTIVA',
                'disponibilidades': [
                    {'dia_semana': 'L', 'hora_inicio': '08:00', 'hora_fin': '20:00'},
                    {'dia_semana': 'S', 'hora_inicio': '09:00', 'hora_fin': '18:00'},
                ],
            },
            format='json',
        )
        self.assertEqual(res_patch.status_code, status.HTTP_200_OK)
        self.assertEqual(res_patch.data['estado_cochera'], 'INACTIVA')
        self.assertEqual(res_patch.data['tarifa_hora'], '50.00')
        self.assertEqual(len(res_patch.data['disponibilidades']), 2)

        # Tenant gets 404 while INACTIVA
        self._auth_as(self.tenant.correo)
        self.assertEqual(
            self.client.get(f'/cocheras/{pending_garage.id_cochera}').status_code,
            status.HTTP_404_NOT_FOUND,
        )

        # Owner reactivates INACTIVA -> APROBADA
        self._auth_as(self.active_landlord.correo)
        res_reactivate = self.client.patch(
            f'/cocheras/{pending_garage.id_cochera}',
            {'estado_cochera': 'APROBADA'},
            format='json',
        )
        self.assertEqual(res_reactivate.status_code, status.HTTP_200_OK)
        self.assertEqual(res_reactivate.data['estado_cochera'], 'APROBADA')

        # Malformed dia_semana in PATCH returns 400 (never 500)
        res_bad_patch = self.client.patch(
            f'/cocheras/{pending_garage.id_cochera}',
            {'disponibilidades': [{'dia_semana': 123, 'hora_inicio': '08:00', 'hora_fin': '12:00'}]},
            format='json',
        )
        self.assertEqual(res_bad_patch.status_code, status.HTTP_400_BAD_REQUEST)
