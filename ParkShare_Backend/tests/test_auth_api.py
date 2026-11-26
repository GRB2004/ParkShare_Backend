import base64
from datetime import date, timedelta
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, override_settings
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

    def test_upload_landlord_documents_persist_and_admin_retrieves_files(self):
        ine_bytes = b'\xff\xd8\xff\xe0\x00\x10JFIF-INE-DEMO-IMAGE'
        csf_bytes = b'%PDF-1.4\n%CSF-CONSTANCIA-FISCAL-DEMO\n%%EOF'
        prop_bytes = b'%PDF-1.4\n%ESCRITURA-PROPIEDAD-DEMO\n%%EOF'

        with tempfile.TemporaryDirectory() as tmp_media:
            with override_settings(MEDIA_ROOT=Path(tmp_media), MEDIA_URL='/media/'):
                # Validation checks on upload endpoint
                missing_file = self.client.post(
                    '/archivos/subir',
                    {'carpeta': 'docs'},
                    format='multipart',
                )
                self.assertEqual(missing_file.status_code, status.HTTP_400_BAD_REQUEST)

                bad_folder = self.client.post(
                    '/archivos/subir',
                    {
                        'carpeta': '../sistema',
                        'archivo': SimpleUploadedFile('ine.pdf', csf_bytes, content_type='application/pdf'),
                    },
                    format='multipart',
                )
                self.assertEqual(bad_folder.status_code, status.HTTP_400_BAD_REQUEST)

                bad_ext = self.client.post(
                    '/archivos/subir',
                    {
                        'carpeta': 'docs',
                        'archivo': SimpleUploadedFile('malware.exe', b'MZ', content_type='application/octet-stream'),
                    },
                    format='multipart',
                )
                self.assertEqual(bad_ext.status_code, status.HTTP_400_BAD_REQUEST)

                # Upload INE, CSF, and Propiedad (unauthenticated public registration flow)
                up_ine = self.client.post(
                    '/archivos/subir',
                    {
                        'carpeta': 'docs',
                        'archivo': SimpleUploadedFile('INE Frontal Carlos.jpg', ine_bytes, content_type='image/jpeg'),
                    },
                    format='multipart',
                )
                self.assertEqual(up_ine.status_code, status.HTTP_201_CREATED)
                self.assertTrue(up_ine.data['url'].startswith('/media/docs/'))
                self.assertTrue(up_ine.data['url'].endswith('.jpg'))

                up_csf = self.client.post(
                    '/api/v1/archivos/subir',
                    {
                        'carpeta': 'docs',
                        'archivo': SimpleUploadedFile('Constancia Fiscal.pdf', csf_bytes, content_type='application/pdf'),
                    },
                    format='multipart',
                )
                self.assertEqual(up_csf.status_code, status.HTTP_201_CREATED)
                self.assertTrue(up_csf.data['url'].startswith('/media/docs/'))
                self.assertTrue(up_csf.data['url'].endswith('.pdf'))

                up_prop = self.client.post(
                    '/archivos/subir',
                    {
                        'carpeta': 'docs',
                        'archivo': SimpleUploadedFile('Escritura_Cochera.pdf', prop_bytes, content_type='application/pdf'),
                    },
                    format='multipart',
                )
                self.assertEqual(up_prop.status_code, status.HTTP_201_CREATED)

                reg_payload = {
                    **self.landlord_payload,
                    'correo': 'carlos.adjuntos@gmail.com',
                    'url_ine': up_ine.data['url'],
                    'url_csf': up_csf.data['url'],
                    'url_propiedad': up_prop.data['url'],
                }
                reg_resp = self.client.post('/auth/registro', reg_payload, format='json')
                self.assertEqual(reg_resp.status_code, status.HTTP_201_CREATED)
                doc_id = reg_resp.data['documento']['id_documento']

                # Admin inspects list & detail and downloads all three stored attachments
                admin_token = self._login_and_get_token('admin@parkshare.mx', 'AdminPassword123!')
                self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {admin_token}')

                with self.assertNumQueries(3):
                    list_resp = self.client.get('/admin/solicitudes')
                self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
                item = list_resp.data['results'][0]
                self.assertEqual(item['url_ine'], up_ine.data['url'])
                self.assertEqual(item['url_csf'], up_csf.data['url'])
                self.assertEqual(item['url_propiedad'], up_prop.data['url'])

                detail_resp = self.client.get(f'/admin/solicitudes/{doc_id}')
                self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
                self.assertEqual(detail_resp.data['url_ine'], up_ine.data['url'])
                self.assertEqual(detail_resp.data['url_csf'], up_csf.data['url'])
                self.assertEqual(detail_resp.data['url_propiedad'], up_prop.data['url'])
                self.assertIn('documentos_adjuntos', detail_resp.data)

                ine_download = self.client.get(detail_resp.data['url_ine'])
                self.assertEqual(ine_download.status_code, status.HTTP_200_OK)
                self.assertEqual(b''.join(ine_download.streaming_content), ine_bytes)
                self.assertIn('image/jpeg', ine_download['Content-Type'])

                csf_download = self.client.get(detail_resp.data['url_csf'])
                self.assertEqual(csf_download.status_code, status.HTTP_200_OK)
                self.assertEqual(b''.join(csf_download.streaming_content), csf_bytes)
                self.assertIn('application/pdf', csf_download['Content-Type'])

                prop_download = self.client.get(detail_resp.data['url_propiedad'])
                self.assertEqual(prop_download.status_code, status.HTTP_200_OK)
                self.assertEqual(b''.join(prop_download.streaming_content), prop_bytes)

                missing_media = self.client.get('/media/docs/no_existe_archivo.pdf')
                self.assertEqual(missing_media.status_code, status.HTTP_404_NOT_FOUND)

    def test_register_landlord_direct_multipart_files_persists_and_cleans_up_on_error(self):
        ine_bytes = b'%PDF-1.4\n%INE-DIRECTA\n%%EOF'
        csf_bytes = b'%PDF-1.4\n%CSF-DIRECTA\n%%EOF'
        prop_bytes = b'%PDF-1.4\n%PROP-DIRECTA\n%%EOF'

        with tempfile.TemporaryDirectory() as tmp_media:
            with override_settings(MEDIA_ROOT=Path(tmp_media), MEDIA_URL='/media/'):
                # Invalid non-gmail with multipart files should fail 400 and leave no orphan files
                bad_resp = self.client.post(
                    '/auth/registro',
                    {
                        'nombre_completo': 'Pedro Arrendador Prueba',
                        'correo': 'pedro@hotmail.com',
                        'telefono': '5511223344',
                        'password': 'MiPasswordSegura123!',
                        'fecha_nacimiento': '1986-04-12',
                        'rol': 'ARRENDADOR',
                        'cuenta_cobro': '012180001122334455',
                        'archivo_ine': SimpleUploadedFile('ine.pdf', ine_bytes, content_type='application/pdf'),
                        'archivo_csf': SimpleUploadedFile('csf.pdf', csf_bytes, content_type='application/pdf'),
                        'archivo_propiedad': SimpleUploadedFile('prop.pdf', prop_bytes, content_type='application/pdf'),
                    },
                    format='multipart',
                )
                self.assertEqual(bad_resp.status_code, status.HTTP_400_BAD_REQUEST)
                docs_dir = Path(tmp_media) / 'docs'
                if docs_dir.exists():
                    self.assertEqual(list(docs_dir.iterdir()), [])

                # Valid direct multipart landlord registration
                ok_resp = self.client.post(
                    '/api/v1/auth/registro',
                    {
                        'nombre_completo': 'Pedro Arrendador Prueba',
                        'correo': 'pedro.multipart@gmail.com',
                        'telefono': '5511223344',
                        'password': 'MiPasswordSegura123!',
                        'fecha_nacimiento': '1986-04-12',
                        'rol': 'ARRENDADOR',
                        'cuenta_cobro': '012180001122334455',
                        'archivo_ine': SimpleUploadedFile('ine.pdf', ine_bytes, content_type='application/pdf'),
                        'archivo_csf': SimpleUploadedFile('csf.pdf', csf_bytes, content_type='application/pdf'),
                        'archivo_propiedad': SimpleUploadedFile('prop.pdf', prop_bytes, content_type='application/pdf'),
                    },
                    format='multipart',
                )
                self.assertEqual(ok_resp.status_code, status.HTTP_201_CREATED)
                doc_data = ok_resp.data['documento']
                self.assertTrue(doc_data['url_ine'].startswith('/media/docs/'))

                admin_token = self._login_and_get_token('admin@parkshare.mx', 'AdminPassword123!')
                self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {admin_token}')
                detail_resp = self.client.get(f"/admin/solicitudes/{doc_data['id_documento']}")
                self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
                dl_resp = self.client.get(detail_resp.data['url_ine'])
                self.assertEqual(dl_resp.status_code, status.HTTP_200_OK)
                self.assertEqual(b''.join(dl_resp.streaming_content), ine_bytes)

    def test_base64_json_pdf_upload_and_max_size_limit_enforcement(self):
        pdf_bytes = b'%PDF-1.4\n%CONSTANCIA-ANDROID-BASE64\n%%EOF'
        pdf_b64 = base64.b64encode(pdf_bytes).decode('ascii')

        with tempfile.TemporaryDirectory() as tmp_media:
            with override_settings(MEDIA_ROOT=Path(tmp_media), MEDIA_URL='/media/'):
                # 1. Android Base64 JSON upload works and serves PDF with Content-Length
                b64_resp = self.client.post(
                    '/archivos/subir',
                    {
                        'carpeta': 'docs',
                        'nombre_archivo': 'ine_constancia_bacm_01_dic_2025.pdf',
                        'mime_type': 'application/pdf',
                        'contenido_base64': pdf_b64,
                    },
                    format='json',
                )
                self.assertEqual(b64_resp.status_code, status.HTTP_201_CREATED)
                self.assertTrue(b64_resp.data['url'].startswith('/media/docs/'))
                self.assertTrue(b64_resp.data['url'].endswith('.pdf'))

                served = self.client.get(b64_resp.data['url'])
                self.assertEqual(served.status_code, status.HTTP_200_OK)
                self.assertEqual(b''.join(served.streaming_content), pdf_bytes)
                self.assertEqual(int(served['Content-Length']), len(pdf_bytes))
                self.assertIn('application/pdf', served['Content-Type'])

                # 2. Oversized file is strictly rejected with 400 in both multipart and Base64 JSON
                oversized_bytes = b'%PDF-1.4\n' + (b'X' * 4096)
                with override_settings(MAX_ATTACHMENT_SIZE_BYTES=2048):
                    too_big_multipart = self.client.post(
                        '/archivos/subir',
                        {
                            'carpeta': 'docs',
                            'archivo': SimpleUploadedFile(
                                'pesado.pdf',
                                oversized_bytes,
                                content_type='application/pdf',
                            ),
                        },
                        format='multipart',
                    )
                    self.assertEqual(too_big_multipart.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertIn('supera el tamaño máximo', too_big_multipart.data['detail'])

                    too_big_b64 = self.client.post(
                        '/archivos/subir',
                        {
                            'carpeta': 'docs',
                            'nombre_archivo': 'pesado.pdf',
                            'mime_type': 'application/pdf',
                            'contenido_base64': base64.b64encode(oversized_bytes).decode('ascii'),
                        },
                        format='json',
                    )
                    self.assertEqual(too_big_b64.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertIn('supera el tamaño máximo', too_big_b64.data['detail'])

    def test_rejected_landlord_resubmits_corrected_documents_and_returns_to_pending_inbox(self):
        with tempfile.TemporaryDirectory() as tmp_media:
            with override_settings(MEDIA_ROOT=tmp_media):
                pdf_bytes = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n'

                reg_resp = self.client.post(
                    '/auth/registro',
                    {
                        'nombre_completo': 'Roberto Arrendador',
                        'correo': 'roberto.rechazado@gmail.com',
                        'telefono': '2223334455',
                        'password': 'PasswordSegura123!',
                        'fecha_nacimiento': '1995-04-10',
                        'rol': 'ARRENDADOR',
                        'archivo_ine': SimpleUploadedFile('ine1.pdf', pdf_bytes, content_type='application/pdf'),
                        'archivo_csf': SimpleUploadedFile('csf1.pdf', pdf_bytes, content_type='application/pdf'),
                        'archivo_propiedad': SimpleUploadedFile('prop1.pdf', pdf_bytes, content_type='application/pdf'),
                    },
                    format='multipart',
                )
                self.assertEqual(reg_resp.status_code, status.HTTP_201_CREATED)
                first_doc_id = reg_resp.data['documento']['id_documento']

                # Admin rechaza la primera solicitud
                admin_login = self.client.post(
                    '/auth/login',
                    {'correo': self.admin.correo, 'password': 'AdminPassword123!'},
                    format='json',
                )
                admin_token = admin_login.data['access']
                reject_resp = self.client.patch(
                    f'/admin/solicitudes/{first_doc_id}',
                    {
                        'estado_verificacion': 'RECHAZADO',
                        'motivo_rechazo': 'INE vencida',
                    },
                    format='json',
                    HTTP_AUTHORIZATION=f'Bearer {admin_token}',
                )
                self.assertEqual(reject_resp.status_code, status.HTTP_200_OK)

                # Arrendador rechazado inicia sesión y recibe estado RECHAZADA + motivo
                host_login = self.client.post(
                    '/auth/login',
                    {'correo': 'roberto.rechazado@gmail.com', 'password': 'PasswordSegura123!'},
                    format='json',
                )
                self.assertEqual(host_login.status_code, status.HTTP_200_OK)
                self.assertEqual(host_login.data['usuario']['estado_cuenta'], 'RECHAZADA')
                self.assertEqual(host_login.data['usuario']['motivo_rechazo'], 'INE vencida')
                host_token = host_login.data['access']

                # Arrendador reenvía únicamente los 3 documentos corregidos en POST /auth/reenviar-documentos
                resubmit_resp = self.client.post(
                    '/auth/reenviar-documentos',
                    {
                        'archivo_ine': SimpleUploadedFile('ine_vigente.pdf', pdf_bytes, content_type='application/pdf'),
                        'archivo_csf': SimpleUploadedFile('csf_actualizada.pdf', pdf_bytes, content_type='application/pdf'),
                        'archivo_propiedad': SimpleUploadedFile('prop_actualizada.pdf', pdf_bytes, content_type='application/pdf'),
                    },
                    format='multipart',
                    HTTP_AUTHORIZATION=f'Bearer {host_token}',
                )
                self.assertEqual(resubmit_resp.status_code, status.HTTP_200_OK)
                self.assertEqual(resubmit_resp.data['estado_cuenta'], 'PENDIENTE')
                self.assertEqual(resubmit_resp.data['documento']['estado_verificacion'], 'PENDIENTE')
                self.assertTrue(resubmit_resp.data['documento']['url_ine'].startswith('/media/docs/'))

                # Verifica que en BD pasó a PENDIENTE y vuelve a aparecer en la bandeja del Administrador
                pending_inbox = self.client.get(
                    '/admin/solicitudes?estado=PENDIENTE',
                    HTTP_AUTHORIZATION=f'Bearer {admin_token}',
                )
                self.assertEqual(pending_inbox.status_code, status.HTTP_200_OK)
                pending_ids = [item['id_documento'] for item in pending_inbox.data['results']]
                self.assertIn(resubmit_resp.data['documento']['id_documento'], pending_ids)

                # Segundo intento mientras ya está PENDIENTE debe devolver 409 Conflict
                duplicate_resp = self.client.post(
                    '/auth/reenviar-documentos',
                    {
                        'url_ine': '/media/docs/ine_vigente.pdf',
                        'url_csf': '/media/docs/csf_actualizada.pdf',
                        'url_propiedad': '/media/docs/prop_actualizada.pdf',
                    },
                    format='json',
                    HTTP_AUTHORIZATION=f'Bearer {host_token}',
                )
                self.assertEqual(duplicate_resp.status_code, status.HTTP_409_CONFLICT)
