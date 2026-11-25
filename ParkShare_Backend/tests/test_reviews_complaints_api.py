from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    ComplaintReasonCategory,
    ComplaintStatus,
    Disponibilidad,
    GarageStatus,
    Queja,
    Resena,
    Reserva,
    ReservationStatus,
    UserRole,
    VehicleType,
    WeekdayChoice,
)

Usuario = get_user_model()
MEXICO_TZ = ZoneInfo('America/Mexico_City')


class ReviewsAndComplaintsAPITests(APITestCase):
    def setUp(self):
        self.password = 'MiPasswordSegura123!'
        self.admin = Usuario.objects.create_user(
            nombre_completo='Admin ParkShare Seis',
            correo='admin.api6@parkshare.mx',
            telefono='5500000006',
            password=self.password,
            fecha_nacimiento=date(1984, 7, 7),
            rol=UserRole.ADMIN,
            estado_cuenta=AccountStatus.ACTIVA,
        )
        self.landlord = Usuario.objects.create_user(
            nombre_completo='Carlos Eduardo Ramírez Pérez',
            correo='carlos.api6@gmail.com',
            telefono='5598765432',
            password=self.password,
            fecha_nacimiento=date(1985, 11, 20),
            rol=UserRole.ARRENDADOR,
            estado_cuenta=AccountStatus.ACTIVA,
            cuenta_cobro='012180001234567890',
        )
        self.other_landlord = Usuario.objects.create_user(
            nombre_completo='Laura Méndez Torres',
            correo='laura.api6@gmail.com',
            telefono='5587654321',
            password=self.password,
            fecha_nacimiento=date(1988, 3, 15),
            rol=UserRole.ARRENDADOR,
            estado_cuenta=AccountStatus.ACTIVA,
            cuenta_cobro='012180009876543210',
        )
        self.tenant = Usuario.objects.create_user(
            nombre_completo='Ana Sofía Martínez López',
            correo='ana.api6@outlook.com',
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
            correo='jorge.api6@outlook.com',
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

        self.start_dt = datetime(2026, 11, 25, 10, 0, tzinfo=MEXICO_TZ)
        self.end_dt = datetime(2026, 11, 25, 12, 0, tzinfo=MEXICO_TZ)

    def _auth_as(self, email: str) -> None:
        res = self.client.post(
            '/auth/login',
            {'correo': email, 'password': self.password},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    def _create_reservation(self, estado: str) -> Reserva:
        return Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.end_dt,
            monto_base=Decimal('80.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=estado,
        )

    # --- POST /reservas/{id}/resenas ---

    def test_post_resenas_bilateral_201_spoofing_protection_and_error_guards(self):
        r_done = self._create_reservation(ReservationStatus.FINALIZADA)
        r_confirmed = self._create_reservation(ReservationStatus.CONFIRMADA)

        # Unauthenticated -> 401
        self.client.credentials()
        self.assertEqual(
            self.client.post(
                f'/reservas/{r_done.id_reserva}/resenas',
                {'calificacion': 5},
                format='json',
            ).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        # Non-participant (other_tenant, other_landlord, admin) -> 403
        for outsider_email in (
            self.other_tenant.correo,
            self.other_landlord.correo,
            self.admin.correo,
        ):
            self._auth_as(outsider_email)
            self.assertEqual(
                self.client.post(
                    f'/reservas/{r_done.id_reserva}/resenas',
                    {'calificacion': 5},
                    format='json',
                ).status_code,
                status.HTTP_403_FORBIDDEN,
            )

        self._auth_as(self.tenant.correo)

        # Non-existent reservation -> 404
        self.assertEqual(
            self.client.post('/reservas/999999/resenas', {'calificacion': 5}, format='json').status_code,
            status.HTTP_404_NOT_FOUND,
        )

        # Reservation not FINALIZADA -> 409 Conflict
        self.assertEqual(
            self.client.post(
                f'/reservas/{r_confirmed.id_reserva}/resenas',
                {'calificacion': 5},
                format='json',
            ).status_code,
            status.HTTP_409_CONFLICT,
        )

        # Invalid calificacion or etiquetas > 255 -> 400 Bad Request
        for bad_payload in (
            {},
            {'calificacion': 0},
            {'calificacion': 6},
            {'calificacion': -1},
            {'calificacion': True},
            {'calificacion': 'cinco'},
            {'calificacion': 5, 'etiquetas': 'A' * 256},
        ):
            self.assertEqual(
                self.client.post(
                    f'/reservas/{r_done.id_reserva}/resenas',
                    bad_payload,
                    format='json',
                ).status_code,
                status.HTTP_400_BAD_REQUEST,
            )

        # 1. Tenant creates review (with spoofed autor_id/destinatario_id in body ignored) -> 201 Created
        res_tenant = self.client.post(
            f'/reservas/{r_done.id_reserva}/resenas',
            {
                'calificacion': 5,
                'etiquetas': 'Puntual,Respetó el espacio,Buen trato',
                'comentario': 'Excelente comunicación y entrega puntual del espacio.',
                'autor_id': 9999,
                'destinatario_id': 9999,
            },
            format='json',
        )
        self.assertEqual(res_tenant.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_tenant.data['reserva_id'], r_done.id_reserva)
        self.assertEqual(res_tenant.data['autor_id'], self.tenant.id_usuario)
        self.assertEqual(res_tenant.data['destinatario_id'], self.landlord.id_usuario)
        self.assertEqual(res_tenant.data['calificacion'], 5)

        # Duplicate review by tenant -> 409 Conflict
        self.assertEqual(
            self.client.post(
                f'/reservas/{r_done.id_reserva}/resenas',
                {'calificacion': 4},
                format='json',
            ).status_code,
            status.HTTP_409_CONFLICT,
        )

        # 2. Landlord creates review via /api/v1/ mirror (minimal payload without optional fields) -> 201 Created
        self._auth_as(self.landlord.correo)
        res_host = self.client.post(
            f'/api/v1/reservas/{r_done.id_reserva}/resenas',
            {'calificacion': 4},
            format='json',
        )
        self.assertEqual(res_host.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_host.data['autor_id'], self.landlord.id_usuario)
        self.assertEqual(res_host.data['destinatario_id'], self.tenant.id_usuario)
        self.assertEqual(res_host.data['calificacion'], 4)
        self.assertEqual(Resena.objects.filter(reserva=r_done).count(), 2)

    # --- POST /quejas ---

    def test_post_quejas_valid_states_201_and_error_guards(self):
        r_confirmed = self._create_reservation(ReservationStatus.CONFIRMADA)
        r_in_use = self._create_reservation(ReservationStatus.EN_USO)
        r_done = self._create_reservation(ReservationStatus.FINALIZADA)
        r_solicitada = self._create_reservation(ReservationStatus.SOLICITADA)
        r_cancelada = self._create_reservation(ReservationStatus.CANCELADA)

        valid_desc = 'Llegué a las 10:05 y el portón permaneció cerrado durante 25 minutos sin respuesta.'

        # Unauthenticated -> 401
        self.client.credentials()
        self.assertEqual(
            self.client.post(
                '/quejas',
                {
                    'reserva_id': r_confirmed.id_reserva,
                    'motivo_categoria': ComplaintReasonCategory.NO_PUDE_ACCEDER,
                    'descripcion': valid_desc,
                },
                format='json',
            ).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        # Landlord or unrelated tenant -> 403
        for forbidden_email in (self.landlord.correo, self.other_tenant.correo):
            self._auth_as(forbidden_email)
            self.assertEqual(
                self.client.post(
                    '/quejas',
                    {
                        'reserva_id': r_confirmed.id_reserva,
                        'motivo_categoria': ComplaintReasonCategory.NO_PUDE_ACCEDER,
                        'descripcion': valid_desc,
                    },
                    format='json',
                ).status_code,
                status.HTTP_403_FORBIDDEN,
            )

        self._auth_as(self.tenant.correo)

        # Non-existent reservation -> 404
        self.assertEqual(
            self.client.post(
                '/quejas',
                {
                    'reserva_id': 999999,
                    'motivo_categoria': ComplaintReasonCategory.NO_PUDE_ACCEDER,
                    'descripcion': valid_desc,
                },
                format='json',
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

        # Invalid states (SOLICITADA, CANCELADA) -> 409 Conflict
        for bad_res in (r_solicitada, r_cancelada):
            self.assertEqual(
                self.client.post(
                    '/quejas',
                    {
                        'reserva_id': bad_res.id_reserva,
                        'motivo_categoria': ComplaintReasonCategory.NO_PUDE_ACCEDER,
                        'descripcion': valid_desc,
                    },
                    format='json',
                ).status_code,
                status.HTTP_409_CONFLICT,
            )

        # Invalid category or description < 10 chars -> 400 Bad Request
        for bad_body in (
            {
                'reserva_id': r_confirmed.id_reserva,
                'motivo_categoria': 'MOTIVO_FALSO',
                'descripcion': valid_desc,
            },
            {
                'reserva_id': r_confirmed.id_reserva,
                'motivo_categoria': ComplaintReasonCategory.OTRO,
                'descripcion': 'Corto',
            },
        ):
            self.assertEqual(
                self.client.post('/quejas', bad_body, format='json').status_code,
                status.HTTP_400_BAD_REQUEST,
            )

        # Valid states (CONFIRMADA, EN_USO, FINALIZADA) -> 201 Created
        for valid_res in (r_confirmed, r_in_use, r_done):
            res_ok = self.client.post(
                '/quejas',
                {
                    'reserva_id': valid_res.id_reserva,
                    'motivo_categoria': ComplaintReasonCategory.NO_PUDE_ACCEDER,
                    'descripcion': valid_desc,
                },
                format='json',
            )
            self.assertEqual(res_ok.status_code, status.HTTP_201_CREATED)
            self.assertEqual(res_ok.data['reserva_id'], valid_res.id_reserva)
            self.assertEqual(res_ok.data['arrendatario_id'], self.tenant.id_usuario)
            self.assertEqual(res_ok.data['arrendador_id'], self.landlord.id_usuario)
            self.assertEqual(res_ok.data['estado'], ComplaintStatus.PENDIENTE)
