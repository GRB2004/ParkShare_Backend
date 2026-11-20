from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.test import RequestFactory
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APITestCase
from rest_framework.views import APIView

from ParkShare_Backend.models import AccountStatus, UserRole, Usuario
from ParkShare_Backend.permissions import IsActiveAccount


def _eighteen_years_ago_str() -> str:
    today = timezone.now().astimezone(ZoneInfo('America/Mexico_City')).date()
    try:
        dob = today.replace(year=today.year - 18)
    except ValueError:
        dob = today.replace(year=today.year - 18, month=2, day=28)
    return dob.isoformat()


def _underage_str() -> str:
    today = timezone.now().astimezone(ZoneInfo('America/Mexico_City')).date()
    try:
        dob = today.replace(year=today.year - 18)
    except ValueError:
        dob = today.replace(year=today.year - 18, month=2, day=28)
    return (dob + timedelta(days=1)).isoformat()


class AuthAPITests(APITestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_user(
            nombre_completo='Administrador Principal',
            correo='admin@parkshare.mx',
            telefono='5500000001',
            password='AdminPassword123!',
            fecha_nacimiento=date(1990, 1, 1),
            rol=UserRole.ADMIN,
        )
        self.tenant_payload = {
            'nombre_completo': 'Ana Sofía Martínez López',
            'correo': 'ana.martinez@outlook.com',
            'telefono': '5512345678',
            'password': 'MiPasswordSegura123!',
            'fecha_nacimiento': '1998-05-14',
            'rol': 'ARRENDATARIO',
            'placas': 'ABC-123-D',
            'modelo_vehiculo': 'Nissan Versa 2022',
        }
        self.landlord_payload = {
            'nombre_completo': 'Carlos Eduardo Ramírez Pérez',
            'correo': 'Carlos.Ramirez@GMAIL.COM',
            'telefono': '5598765432',
            'password': 'MiPasswordSegura123!',
            'fecha_nacimiento': '1985-11-20',
            'rol': 'ARRENDADOR',
            'cuenta_cobro': '012180001234567890',
            'url_ine': 'https://storage.parkshare.mx/docs/carlos_ine.jpg',
            'url_csf': 'https://storage.parkshare.mx/docs/carlos_csf.pdf',
            'url_propiedad': 'https://storage.parkshare.mx/docs/carlos_escritura.pdf',
        }

    def _login_and_get_token(self, correo: str, password: str) -> str:
        resp = self.client.post('/auth/login', {'correo': correo, 'password': password}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp.data['access']

    def test_register_tenant_and_login_happy_path(self):
        resp = self.client.post('/auth/registro', self.tenant_payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['usuario']['estado_cuenta'], 'ACTIVA')
        self.assertEqual(resp.data['usuario']['placas'], 'ABC-123-D')
        self.assertNotIn('password_hash', resp.data['usuario'])
        self.assertNotIn('password', resp.data['usuario'])

        login_resp = self.client.post(
            '/auth/login',
            {'correo': 'ANA.MARTINEZ@outlook.com', 'password': 'MiPasswordSegura123!'},
            format='json',
        )
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK)
        self.assertIn('access', login_resp.data)
        self.assertIn('refresh', login_resp.data)
        self.assertEqual(login_resp.data['usuario']['estado_cuenta'], 'ACTIVA')
        self.assertIsNone(login_resp.data['usuario']['motivo_rechazo'])

    def test_register_landlord_normalizes_email_and_creates_pending_request(self):
        resp = self.client.post('/auth/registro', self.landlord_payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['usuario']['correo'], 'carlos.ramirez@gmail.com')
        self.assertEqual(resp.data['usuario']['estado_cuenta'], 'PENDIENTE')
        self.assertEqual(resp.data['documento']['estado_verificacion'], 'PENDIENTE')

    def test_register_exact_18_birthday_passes_and_underage_fails(self):
        payload_18 = {
            **self.tenant_payload,
            'correo': 'exact18@outlook.com',
            'fecha_nacimiento': _eighteen_years_ago_str(),
        }
        resp_ok = self.client.post('/auth/registro', payload_18, format='json')
        self.assertEqual(resp_ok.status_code, status.HTTP_201_CREATED)

        payload_17 = {
            **self.tenant_payload,
            'correo': 'menor17@outlook.com',
            'fecha_nacimiento': _underage_str(),
        }
        resp_fail = self.client.post('/auth/registro', payload_17, format='json')
        self.assertEqual(resp_fail.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('fecha_nacimiento', resp_fail.data['errors'])

    def test_register_validation_errors(self):
        non_gmail = {**self.landlord_payload, 'correo': 'carlos@hotmail.com'}
        r1 = self.client.post('/auth/registro', non_gmail, format='json')
        self.assertEqual(r1.status_code, status.HTTP_400_BAD_REQUEST)

        bad_name = {**self.tenant_payload, 'correo': 'badname@outlook.com', 'nombre_completo': 'Juan Pérez 123'}
        r2 = self.client.post('/auth/registro', bad_name, format='json')
        self.assertEqual(r2.status_code, status.HTTP_400_BAD_REQUEST)

        bad_phone = {**self.tenant_payload, 'correo': 'badphone@outlook.com', 'telefono': '12345'}
        r3 = self.client.post('/auth/registro', bad_phone, format='json')
        self.assertEqual(r3.status_code, status.HTTP_400_BAD_REQUEST)

        self.client.post('/auth/registro', self.tenant_payload, format='json')
        dup_email = {**self.tenant_payload, 'correo': 'ANA.MARTINEZ@OUTLOOK.COM'}
        r4 = self.client.post('/auth/registro', dup_email, format='json')
        self.assertEqual(r4.status_code, status.HTTP_400_BAD_REQUEST)

        bad_doc = {**self.landlord_payload, 'correo': 'baddoc@gmail.com', 'url_csf': 'https://s.mx/csf.jpg'}
        r5 = self.client.post('/auth/registro', bad_doc, format='json')
        self.assertEqual(r5.status_code, status.HTTP_400_BAD_REQUEST)

        admin_attempt = {**self.tenant_payload, 'correo': 'hacker@outlook.com', 'rol': 'ADMIN'}
        r6 = self.client.post('/auth/registro', admin_attempt, format='json')
        self.assertEqual(r6.status_code, status.HTTP_400_BAD_REQUEST)

        no_vehicle = {
            'nombre_completo': 'Sin Auto',
            'correo': 'sinauto@outlook.com',
            'telefono': '5512345678',
            'password': 'MiPasswordSegura123!',
            'fecha_nacimiento': '1998-05-14',
            'rol': 'ARRENDATARIO',
        }
        r7 = self.client.post('/auth/registro', no_vehicle, format='json')
        self.assertEqual(r7.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_invalid_credentials_returns_401(self):
        self.client.post('/auth/registro', self.tenant_payload, format='json')
        resp = self.client.post(
            '/auth/login',
            {'correo': 'ana.martinez@outlook.com', 'password': 'Incorrecta!'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_solicitudes_list_filters_detail_approve_and_role_guards(self):
        reg = self.client.post('/auth/registro', self.landlord_payload, format='json')
        doc_id = reg.data['documento']['id_documento']

        unauth = self.client.get('/admin/solicitudes')
        self.assertEqual(unauth.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.post('/auth/registro', self.tenant_payload, format='json')
        tenant_token = self._login_and_get_token('ana.martinez@outlook.com', 'MiPasswordSegura123!')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {tenant_token}')
        self.assertEqual(self.client.get('/admin/solicitudes').status_code, status.HTTP_403_FORBIDDEN)

        admin_token = self._login_and_get_token('admin@parkshare.mx', 'AdminPassword123!')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {admin_token}')

        with self.assertNumQueries(3):
            list_resp = self.client.get('/admin/solicitudes')
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(list_resp.data['count'], 1)
        self.assertEqual(list_resp.data['results'][0]['id_documento'], doc_id)

        detail_resp = self.client.get(f'/admin/solicitudes/{doc_id}')
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertIn('url_ine', detail_resp.data)
        self.assertIn('url_csf', detail_resp.data)
        self.assertIn('url_propiedad', detail_resp.data)

        self.assertEqual(self.client.get('/admin/solicitudes/999999').status_code, status.HTTP_404_NOT_FOUND)

        approve_resp = self.client.patch(
            f'/admin/solicitudes/{doc_id}',
            {'estado_verificacion': 'APROBADO'},
            format='json',
        )
        self.assertEqual(approve_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(approve_resp.data['estado_verificacion'], 'APROBADO')
        self.assertEqual(approve_resp.data['arrendador_estado_cuenta'], 'ACTIVA')

        # Arrendador activo tampoco puede acceder a endpoints /admin/...
        landlord_token = self._login_and_get_token('carlos.ramirez@gmail.com', 'MiPasswordSegura123!')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {landlord_token}')
        self.assertEqual(self.client.get('/admin/solicitudes').status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get('/admin/cuentas').status_code, status.HTTP_403_FORBIDDEN)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {admin_token}')

        # Filtros ?estado=APROBADO, ?estado=TODOS, ?estado=RECHAZADO
        self.assertEqual(self.client.get('/admin/solicitudes?estado=APROBADO').data['count'], 1)
        self.assertEqual(self.client.get('/admin/solicitudes?estado=RECHAZADO').data['count'], 0)
        self.assertEqual(self.client.get('/admin/solicitudes?estado=TODOS').data['count'], 1)

        conflict_resp = self.client.patch(
            f'/admin/solicitudes/{doc_id}',
            {'estado_verificacion': 'RECHAZADO', 'motivo_rechazo': 'INE vencida'},
            format='json',
        )
        self.assertEqual(conflict_resp.status_code, status.HTTP_409_CONFLICT)

    def test_admin_reject_solicitud_validations_and_login_motivo(self):
        payload2 = {**self.landlord_payload, 'correo': 'segundo@gmail.com'}
        reg = self.client.post('/auth/registro', payload2, format='json')
        doc_id = reg.data['documento']['id_documento']

        admin_token = self._login_and_get_token('admin@parkshare.mx', 'AdminPassword123!')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {admin_token}')

        self.assertEqual(
            self.client.patch(f'/admin/solicitudes/{doc_id}', {'estado_verificacion': 'RECHAZADO'}, format='json').status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.patch(
                f'/admin/solicitudes/{doc_id}',
                {'estado_verificacion': 'RECHAZADO', 'motivo_rechazo': 'Otro'},
                format='json',
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        r_ok = self.client.patch(
            f'/admin/solicitudes/{doc_id}',
            {
                'estado_verificacion': 'RECHAZADO',
                'motivo_rechazo': 'Otro',
                'detalle_motivo': 'La constancia fiscal está borrosa.',
            },
            format='json',
        )
        self.assertEqual(r_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(r_ok.data['arrendador_estado_cuenta'], 'RECHAZADA')

        self.assertEqual(self.client.get('/admin/solicitudes?estado=RECHAZADO').data['count'], 1)

        self.client.credentials()
        login_resp = self.client.post(
            '/auth/login',
            {'correo': 'segundo@gmail.com', 'password': 'MiPasswordSegura123!'},
            format='json',
        )
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(login_resp.data['usuario']['estado_cuenta'], 'RECHAZADA')
        self.assertIn('La constancia fiscal está borrosa.', login_resp.data['usuario']['motivo_rechazo'])

    def test_admin_cuentas_search_filters_detail_num_queries_and_suspend_live_invalidation(self):
        reg = self.client.post('/auth/registro', self.tenant_payload, format='json')
        tenant_id = reg.data['usuario']['id_usuario']
        self.client.post('/auth/registro', self.landlord_payload, format='json')
        tenant_token = self._login_and_get_token('ana.martinez@outlook.com', 'MiPasswordSegura123!')

        admin_token = self._login_and_get_token('admin@parkshare.mx', 'AdminPassword123!')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {admin_token}')

        search_resp = self.client.get('/admin/cuentas?q=ana')
        self.assertEqual(search_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(search_resp.data['count'], 1)
        self.assertEqual(search_resp.data['results'][0]['id_usuario'], tenant_id)

        # Filtros ?rol= y ?estado_cuenta=
        role_resp = self.client.get('/admin/cuentas?rol=ARRENDATARIO&estado_cuenta=ACTIVA')
        self.assertEqual(role_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(role_resp.data['count'], 1)
        self.assertEqual(self.client.get('/admin/cuentas?rol=ARRENDADOR&estado_cuenta=PENDIENTE').data['count'], 1)

        # Detalle de cuenta antes de suspender
        detail_resp = self.client.get(f'/admin/cuentas/{tenant_id}')
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_resp.data['resumen_disciplinario']['total_suspensiones'], 0)

        # Validaciones de suspensión
        self.assertEqual(
            self.client.post(f'/admin/cuentas/{tenant_id}/suspender', {'motivo': ''}, format='json').status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.post(f'/admin/cuentas/{self.admin.id_usuario}/suspender', {'motivo': 'Prueba'}, format='json').status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        susp_resp = self.client.post(
            f'/admin/cuentas/{tenant_id}/suspender',
            {'motivo': 'Reincidencia en daños reportados.'},
            format='json',
        )
        self.assertEqual(susp_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(susp_resp.data['estado_cuenta'], 'SUSPENDIDA')
        self.assertEqual(susp_resp.data['suspension']['estado'], 'ACTIVA')

        # Verificar prefetch_related en GET /admin/cuentas/{id} con suspensión registrada (<= 3 queries: auth user + target user + prefetch)
        with self.assertNumQueries(3):
            detail_after = self.client.get(f'/admin/cuentas/{tenant_id}')
        self.assertEqual(detail_after.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_after.data['resumen_disciplinario']['total_suspensiones'], 1)
        self.assertEqual(len(detail_after.data['historial_suspensiones']), 1)

        # Doble suspensión -> 409
        self.assertEqual(
            self.client.post(f'/admin/cuentas/{tenant_id}/suspender', {'motivo': 'Otro'}, format='json').status_code,
            status.HTTP_409_CONFLICT,
        )

        # Token previo invalidado de inmediato
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {tenant_token}')
        self.assertEqual(self.client.get('/admin/cuentas').status_code, status.HTTP_401_UNAUTHORIZED)

        # Login posterior -> 403
        self.client.credentials()
        blocked_login = self.client.post(
            '/auth/login',
            {'correo': 'ana.martinez@outlook.com', 'password': 'MiPasswordSegura123!'},
            format='json',
        )
        self.assertEqual(blocked_login.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(blocked_login.data['codigo'], 'CUENTA_SUSPENDIDA')
        self.assertEqual(blocked_login.data['estado_cuenta'], 'SUSPENDIDA')

    def test_api_v1_prefix_routes_also_work(self):
        resp = self.client.post('/api/v1/auth/registro', self.tenant_payload, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        login_resp = self.client.post(
            '/api/v1/auth/login',
            {'correo': 'ana.martinez@outlook.com', 'password': 'MiPasswordSegura123!'},
            format='json',
        )
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK)

    def test_pending_landlord_is_blocked_by_is_active_account_permission(self):
        self.client.post('/auth/registro', self.landlord_payload, format='json')
        landlord_token = self._login_and_get_token('carlos.ramirez@gmail.com', 'MiPasswordSegura123!')

        class DummyProtectedView(APIView):
            permission_classes = [IsAuthenticated, IsActiveAccount]

            def get(self, request):
                return Response({'ok': True})

        user = Usuario.objects.get(correo='carlos.ramirez@gmail.com')
        factory = RequestFactory()
        req = factory.get('/protected/', HTTP_AUTHORIZATION=f'Bearer {landlord_token}')
        view_resp = DummyProtectedView.as_view()(req)
        self.assertEqual(view_resp.status_code, status.HTTP_403_FORBIDDEN)

        user.estado_cuenta = AccountStatus.ACTIVA
        user.save()
        req2 = factory.get('/protected/', HTTP_AUTHORIZATION=f'Bearer {landlord_token}')
        view_resp2 = DummyProtectedView.as_view()(req2)
        self.assertEqual(view_resp2.status_code, status.HTTP_200_OK)
