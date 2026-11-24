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
    Pago,
    PaymentStatus,
    QRCodeType,
    Reserva,
    ReservationStatus,
    RetiroCobro,
    UserRole,
    VehicleType,
    WeekdayChoice,
    WithdrawalStatus,
)
from ParkShare_Backend.services.reserva_service import create_reservation

Usuario = get_user_model()
MEXICO_TZ = ZoneInfo('America/Mexico_City')


class PaymentAPITests(APITestCase):
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
            cuenta_cobro='0121800012344321',
        )
        self.landlord_no_account = Usuario.objects.create_user(
            nombre_completo='Miguel Ángel Torres',
            correo='miguel.torres@gmail.com',
            telefono='5587654321',
            password=self.password,
            fecha_nacimiento=date(1988, 3, 15),
            rol=UserRole.ARRENDADOR,
            estado_cuenta=AccountStatus.ACTIVA,
            cuenta_cobro=None,
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

        self.garage = self._create_garage(self.landlord)
        self.garage_no_account = self._create_garage(self.landlord_no_account)

        tomorrow = (timezone.now().astimezone(MEXICO_TZ) + timedelta(days=1)).date()
        self.start_dt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 16, 0, tzinfo=MEXICO_TZ)
        self.end_dt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 18, 0, tzinfo=MEXICO_TZ)

    def _create_garage(self, landlord: Usuario, rate: str = '40.00') -> Cochera:
        garage = Cochera.objects.create(
            arrendador=landlord,
            nombre='Cochera Centro Norte',
            direccion='Av. Insurgentes Sur 1450',
            latitud=Decimal('19.37124500'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('35.50'),
            espacios_totales=3,
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

    def _auth_as(self, email: str) -> None:
        res = self.client.post(
            '/auth/login',
            {'correo': email, 'password': self.password},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    # --- POST /pagos ---

    def test_post_pagos_approved_201_and_rejected_402_and_guards(self):
        created_at = self.start_dt - timedelta(hours=2)
        r1 = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=created_at
        )

        # Unauthenticated -> 401
        self.client.credentials()
        self.assertEqual(
            self.client.post(
                '/pagos',
                {'reserva_id': r1.id_reserva, 'metodo': 'TARJETA', 'token_pago': 'tok_visa_4242'},
                format='json',
            ).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        # Landlord or unrelated tenant -> 403
        for forbidden_email in (self.landlord.correo, self.other_tenant.correo):
            self._auth_as(forbidden_email)
            self.assertEqual(
                self.client.post(
                    '/pagos',
                    {'reserva_id': r1.id_reserva, 'metodo': 'TARJETA', 'token_pago': 'tok_visa_4242'},
                    format='json',
                ).status_code,
                status.HTTP_403_FORBIDDEN,
            )

        self._auth_as(self.tenant.correo)

        # Non-existent reservation -> 404
        self.assertEqual(
            self.client.post(
                '/pagos',
                {'reserva_id': 999999, 'metodo': 'TARJETA'},
                format='json',
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

        # Bad payload / invalid method -> 400
        for bad_payload in (
            {},
            {'reserva_id': r1.id_reserva},
            {'reserva_id': r1.id_reserva, 'metodo': ''},
            {'reserva_id': r1.id_reserva, 'metodo': 'BITCOIN'},
        ):
            self.assertEqual(
                self.client.post('/pagos', bad_payload, format='json').status_code,
                status.HTTP_400_BAD_REQUEST,
            )

        # Approved payment -> 201 Created, confirms reservation and generates ENTRADA QR
        res_ok = self.client.post(
            '/pagos',
            {'reserva_id': r1.id_reserva, 'metodo': 'TARJETA', 'token_pago': 'tok_visa_4242'},
            format='json',
        )
        self.assertEqual(res_ok.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_ok.data['reserva_id'], r1.id_reserva)
        self.assertEqual(res_ok.data['monto_total'], '80.00')
        self.assertEqual(res_ok.data['metodo'], 'TARJETA')
        self.assertEqual(res_ok.data['estado'], PaymentStatus.APROBADO)
        self.assertEqual(res_ok.data['estado_reserva'], ReservationStatus.CONFIRMADA)
        self.assertTrue(res_ok.data['qr_entrada_generado'])
        self.assertTrue(res_ok.data['referencia_pasarela'].startswith('PS-PAY-'))

        r1.refresh_from_db()
        self.assertEqual(r1.estado, ReservationStatus.CONFIRMADA)
        self.assertTrue(
            CodigoQR.objects.filter(reserva=r1, tipo_qr=QRCodeType.ENTRADA, usado=False).exists()
        )

        # Duplicate payment on already confirmed reservation -> 409 Conflict
        res_dup = self.client.post(
            '/pagos',
            {'reserva_id': r1.id_reserva, 'metodo': 'TARJETA', 'token_pago': 'tok_visa_4242'},
            format='json',
        )
        self.assertEqual(res_dup.status_code, status.HTTP_409_CONFLICT)

        # Rejected payment via /api/v1/pagos -> 402 Payment Required, cancels reservation, no QR
        r2 = create_reservation(
            self.other_tenant,
            self.garage.id_cochera,
            self.start_dt + timedelta(hours=3),
            self.end_dt + timedelta(hours=3),
            now=created_at,
        )
        self._auth_as(self.other_tenant.correo)
        res_decl = self.client.post(
            '/api/v1/pagos',
            {'reserva_id': r2.id_reserva, 'metodo': 'TARJETA', 'token_pago': 'tok_rechazado'},
            format='json',
        )
        self.assertEqual(res_decl.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(res_decl.data['estado'], PaymentStatus.RECHAZADO)
        self.assertEqual(res_decl.data['estado_reserva'], ReservationStatus.CANCELADA)
        self.assertFalse(res_decl.data['qr_entrada_generado'])

        r2.refresh_from_db()
        self.assertEqual(r2.estado, ReservationStatus.CANCELADA)
        self.assertFalse(CodigoQR.objects.filter(reserva=r2).exists())

    # --- GET /arrendador/cobros & POST /arrendador/cobros/retirar ---

    def test_host_cobros_dashboard_and_withdrawal_lifecycle(self):
        now_dt = datetime(2026, 11, 25, 20, 0, tzinfo=MEXICO_TZ)

        # 1. Finalized today with late checkout surcharge ($80 base + $100 penalty = $180)
        r_today = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=datetime(2026, 11, 25, 16, 0, tzinfo=MEXICO_TZ),
            fecha_hora_fin=datetime(2026, 11, 25, 18, 0, tzinfo=MEXICO_TZ),
            hora_salida_real=datetime(2026, 11, 25, 18, 20, tzinfo=MEXICO_TZ),
            monto_base=Decimal('80.00'),
            monto_penalizacion=Decimal('100.00'),
            estado=ReservationStatus.FINALIZADA,
        )
        Pago.objects.create(
            reserva=r_today,
            monto_total=Decimal('180.00'),
            metodo='TARJETA',
            referencia_pasarela='PS-PAY-101',
            estado=PaymentStatus.APROBADO,
            fecha_pago=datetime(2026, 11, 25, 15, 0, tzinfo=MEXICO_TZ),
        )

        # 2. Finalized yesterday ($240)
        r_yest = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=datetime(2026, 11, 24, 10, 0, tzinfo=MEXICO_TZ),
            fecha_hora_fin=datetime(2026, 11, 24, 16, 0, tzinfo=MEXICO_TZ),
            hora_salida_real=datetime(2026, 11, 24, 16, 0, tzinfo=MEXICO_TZ),
            monto_base=Decimal('240.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.FINALIZADA,
        )
        Pago.objects.create(
            reserva=r_yest,
            monto_total=Decimal('240.00'),
            metodo='TARJETA',
            referencia_pasarela='PS-PAY-102',
            estado=PaymentStatus.APROBADO,
            fecha_pago=datetime(2026, 11, 24, 9, 30, tzinfo=MEXICO_TZ),
        )

        # 3. Confirmed upcoming ($70) -> Pendiente de uso
        r_upcoming = Reserva.objects.create(
            arrendatario=self.other_tenant,
            cochera=self.garage,
            fecha_hora_inicio=datetime(2026, 11, 26, 10, 0, tzinfo=MEXICO_TZ),
            fecha_hora_fin=datetime(2026, 11, 26, 12, 0, tzinfo=MEXICO_TZ),
            monto_base=Decimal('70.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.CONFIRMADA,
        )
        Pago.objects.create(
            reserva=r_upcoming,
            monto_total=Decimal('70.00'),
            metodo='TARJETA',
            referencia_pasarela='PS-PAY-103',
            estado=PaymentStatus.APROBADO,
            fecha_pago=datetime(2026, 11, 25, 19, 0, tzinfo=MEXICO_TZ),
        )

        # Unauthenticated -> 401
        self.client.credentials()
        self.assertEqual(self.client.get('/arrendador/cobros').status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            self.client.post('/arrendador/cobros/retirar', {}, format='json').status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

        # Tenant or pending landlord -> 403
        for forbidden_email in (self.tenant.correo, self.pending_landlord.correo):
            self._auth_as(forbidden_email)
            self.assertEqual(self.client.get('/arrendador/cobros').status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(
                self.client.post('/arrendador/cobros/retirar', {}, format='json').status_code,
                status.HTTP_403_FORBIDDEN,
            )

        # Active landlord fetches financial dashboard
        self._auth_as(self.landlord.correo)
        with patch('django.utils.timezone.now', return_value=now_dt):
            res_dash = self.client.get('/arrendador/cobros')
        self.assertEqual(res_dash.status_code, status.HTTP_200_OK)
        self.assertEqual(res_dash.data['ganado_hoy'], '180.00')
        self.assertEqual(res_dash.data['disponible_para_retirar'], '420.00')
        self.assertEqual(res_dash.data['cuenta_cobro'], '****4321')
        self.assertEqual(len(res_dash.data['movimientos']), 3)

        # Filter ?estado=Pagado keeps global totals intact
        with patch('django.utils.timezone.now', return_value=now_dt):
            res_paid = self.client.get('/api/v1/arrendador/cobros?estado=Pagado')
        self.assertEqual(res_paid.status_code, status.HTTP_200_OK)
        self.assertEqual(res_paid.data['ganado_hoy'], '180.00')
        self.assertEqual(res_paid.data['disponible_para_retirar'], '420.00')
        self.assertEqual(len(res_paid.data['movimientos']), 2)
        self.assertTrue(all(m['estado'] == 'Pagado' for m in res_paid.data['movimientos']))

        # Invalid filter -> 400
        self.assertEqual(
            self.client.get('/arrendador/cobros?estado=Invalido').status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        # Withdraw available balance -> 201 Created
        with patch('django.utils.timezone.now', return_value=now_dt):
            res_withdraw = self.client.post('/arrendador/cobros/retirar', {}, format='json')
        self.assertEqual(res_withdraw.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_withdraw.data['monto_retirado'], '420.00')
        self.assertEqual(res_withdraw.data['cuenta_cobro'], '****4321')
        self.assertEqual(res_withdraw.data['estado'], WithdrawalStatus.COMPLETADO)
        self.assertEqual(res_withdraw.data['disponible_para_retirar'], '0.00')
        self.assertTrue(res_withdraw.data['referencia'].startswith('PS-RET-'))

        # Second immediate withdrawal -> 409 Conflict (NoFundsAvailable)
        res_dup_withdraw = self.client.post('/arrendador/cobros/retirar', {}, format='json')
        self.assertEqual(res_dup_withdraw.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res_dup_withdraw.data['error'], 'NoFundsAvailable')

    def test_withdraw_missing_or_invalid_payout_account_400_and_inline_save_201(self):
        r_done = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage_no_account,
            fecha_hora_inicio=datetime(2026, 11, 24, 10, 0, tzinfo=MEXICO_TZ),
            fecha_hora_fin=datetime(2026, 11, 24, 12, 0, tzinfo=MEXICO_TZ),
            hora_salida_real=datetime(2026, 11, 24, 12, 0, tzinfo=MEXICO_TZ),
            monto_base=Decimal('80.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.FINALIZADA,
        )
        Pago.objects.create(
            reserva=r_done,
            monto_total=Decimal('80.00'),
            metodo='TARJETA',
            referencia_pasarela='PS-PAY-201',
            estado=PaymentStatus.APROBADO,
        )

        self._auth_as(self.landlord_no_account.correo)

        # Missing cuenta_cobro in profile and payload -> 400 MissingPayoutAccount
        res_missing = self.client.post('/arrendador/cobros/retirar', {}, format='json')
        self.assertEqual(res_missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res_missing.data['error'], 'MissingPayoutAccount')

        # Invalid format (too short / letters) -> 400 MissingPayoutAccount
        res_bad = self.client.post(
            '/arrendador/cobros/retirar',
            {'cuenta_cobro': '12345ABC'},
            format='json',
        )
        self.assertEqual(res_bad.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res_bad.data['error'], 'MissingPayoutAccount')

        # Providing valid 18-digit CLABE saves it and processes withdrawal -> 201 Created
        res_ok = self.client.post(
            '/api/v1/arrendador/cobros/retirar',
            {'cuenta_cobro': '012180009999888878'},
            format='json',
        )
        self.assertEqual(res_ok.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_ok.data['monto_retirado'], '80.00')
        self.assertEqual(res_ok.data['cuenta_cobro'], '****8878')

        self.landlord_no_account.refresh_from_db()
        self.assertEqual(self.landlord_no_account.cuenta_cobro, '012180009999888878')
