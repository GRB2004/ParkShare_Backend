from datetime import date

from django.test import RequestFactory, TestCase
from rest_framework.exceptions import AuthenticationFailed

from ParkShare_Backend.authentication import CustomJWTAuthentication, generate_tokens_for_user
from ParkShare_Backend.models import (
    AccountStatus,
    SuspensionStatus,
    UserRole,
    Usuario,
    VerificationStatus,
)
from ParkShare_Backend.permissions import (
    IsActiveAccount,
    IsActiveLandlord,
    IsActiveTenant,
    IsAdminRole,
)
from ParkShare_Backend.services.auth_service import (
    AccountSuspendedError,
    AuthenticationError,
    ConflictError,
    DomainValidationError,
    authenticate_user,
    register_user,
    review_landlord_request,
    suspend_account,
)


class AuthServicesTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = Usuario.objects.create_user(
            nombre_completo='Administrador Uno',
            correo='admin@parkshare.mx',
            telefono='5500000001',
            password='AdminPassword123!',
            fecha_nacimiento=date(1990, 1, 1),
            rol=UserRole.ADMIN,
        )

    def test_register_tenant_creates_active_user(self):
        result = register_user({
            'nombre_completo': 'Ana Sofía Martínez',
            'correo': 'Ana@Outlook.com',
            'telefono': '5512345678',
            'password': 'MiPasswordSegura123!',
            'fecha_nacimiento': date(1998, 5, 14),
            'rol': UserRole.ARRENDATARIO,
            'placas': 'ABC-123-D',
            'modelo_vehiculo': 'Nissan Versa 2022',
        })
        user = result['usuario']

        self.assertEqual(user.estado_cuenta, AccountStatus.ACTIVA)
        self.assertEqual(user.correo, 'ana@outlook.com')
        self.assertIsNone(result.get('documento'))

    def test_register_landlord_creates_pending_user_and_documents_atomically(self):
        result = register_user({
            'nombre_completo': 'Carlos Ramírez',
            'correo': 'Carlos@Gmail.com',
            'telefono': '5598765432',
            'password': 'MiPasswordSegura123!',
            'fecha_nacimiento': date(1985, 11, 20),
            'rol': UserRole.ARRENDADOR,
            'cuenta_cobro': '012180001234567890',
            'url_ine': 'https://storage.parkshare.mx/ine.jpg',
            'url_csf': 'https://storage.parkshare.mx/csf.pdf',
            'url_propiedad': 'https://storage.parkshare.mx/prop.pdf',
        })
        user = result['usuario']
        doc = result['documento']

        self.assertEqual(user.estado_cuenta, AccountStatus.PENDIENTE)
        self.assertEqual(doc.estado_verificacion, VerificationStatus.PENDIENTE)
        self.assertEqual(doc.arrendador_id, user.id_usuario)

    def test_register_landlord_validates_documents_without_persisting_user(self):
        initial_count = Usuario.objects.count()
        with self.assertRaises(DomainValidationError):
            register_user({
                'nombre_completo': 'Carlos Ramírez',
                'correo': 'carlos.bad@gmail.com',
                'telefono': '5598765432',
                'password': 'MiPasswordSegura123!',
                'fecha_nacimiento': date(1985, 11, 20),
                'rol': UserRole.ARRENDADOR,
                'url_ine': 'https://storage.parkshare.mx/ine.jpg',
                'url_csf': 'https://storage.parkshare.mx/csf.jpg',
                'url_propiedad': 'https://storage.parkshare.mx/prop.pdf',
            })
        self.assertEqual(Usuario.objects.count(), initial_count)

    def test_register_admin_role_is_rejected(self):
        with self.assertRaises(DomainValidationError):
            register_user({
                'nombre_completo': 'Intruso Admin',
                'correo': 'intruso@gmail.com',
                'telefono': '5511223344',
                'password': 'MiPasswordSegura123!',
                'fecha_nacimiento': date(1990, 1, 1),
                'rol': UserRole.ADMIN,
            })

    def test_authenticate_user_success_and_invalid_credentials(self):
        register_user({
            'nombre_completo': 'Ana Martínez',
            'correo': 'ana@outlook.com',
            'telefono': '5512345678',
            'password': 'MiPasswordSegura123!',
            'fecha_nacimiento': date(1998, 5, 14),
            'rol': UserRole.ARRENDATARIO,
            'placas': 'ABC-123-D',
            'modelo_vehiculo': 'Nissan Versa 2022',
        })

        auth_result = authenticate_user('ANA@outlook.com', 'MiPasswordSegura123!')
        self.assertIn('access', auth_result)
        self.assertIn('refresh', auth_result)
        self.assertEqual(auth_result['usuario'].correo, 'ana@outlook.com')

        with self.assertRaises(AuthenticationError):
            authenticate_user('ana@outlook.com', 'WrongPassword!')

    def test_review_landlord_request_approve_and_reject_with_conflict(self):
        reg = register_user({
            'nombre_completo': 'Carlos Ramírez',
            'correo': 'carlos@gmail.com',
            'telefono': '5598765432',
            'password': 'MiPasswordSegura123!',
            'fecha_nacimiento': date(1985, 11, 20),
            'rol': UserRole.ARRENDADOR,
            'url_ine': 'https://storage.parkshare.mx/ine.jpg',
            'url_csf': 'https://storage.parkshare.mx/csf.pdf',
            'url_propiedad': 'https://storage.parkshare.mx/prop.pdf',
        })
        doc = reg['documento']

        approved_doc = review_landlord_request(doc.id_documento, VerificationStatus.APROBADO)
        self.assertEqual(approved_doc.estado_verificacion, VerificationStatus.APROBADO)
        self.assertEqual(approved_doc.arrendador.estado_cuenta, AccountStatus.ACTIVA)

        with self.assertRaises(ConflictError):
            review_landlord_request(
                doc.id_documento,
                VerificationStatus.RECHAZADO,
                rejection_reason='INE vencida',
            )

    def test_review_landlord_request_reject_other_reason_and_login_shows_reason(self):
        reg = register_user({
            'nombre_completo': 'Marta Díaz',
            'correo': 'marta@gmail.com',
            'telefono': '5598765433',
            'password': 'MiPasswordSegura123!',
            'fecha_nacimiento': date(1987, 4, 12),
            'rol': UserRole.ARRENDADOR,
            'url_ine': 'https://storage.parkshare.mx/ine.jpg',
            'url_csf': 'https://storage.parkshare.mx/csf.pdf',
            'url_propiedad': 'https://storage.parkshare.mx/prop.pdf',
        })
        doc = reg['documento']

        with self.assertRaises(DomainValidationError):
            review_landlord_request(
                doc.id_documento,
                VerificationStatus.RECHAZADO,
                rejection_reason='Otro',
            )

        rejected_doc = review_landlord_request(
            doc.id_documento,
            VerificationStatus.RECHAZADO,
            rejection_reason='Otro',
            reason_detail='Documento ilegible.',
        )
        self.assertEqual(rejected_doc.estado_verificacion, VerificationStatus.RECHAZADO)
        self.assertIn('Documento ilegible.', rejected_doc.motivo_rechazo)
        self.assertEqual(rejected_doc.arrendador.estado_cuenta, AccountStatus.RECHAZADA)

        login_data = authenticate_user('marta@gmail.com', 'MiPasswordSegura123!')
        self.assertEqual(login_data['usuario'].estado_cuenta, AccountStatus.RECHAZADA)
        self.assertIn('Documento ilegible.', login_data['motivo_rechazo'])

    def test_review_suspended_landlord_request_raises_conflict(self):
        reg = register_user({
            'nombre_completo': 'Jorge Ramos',
            'correo': 'jorge@gmail.com',
            'telefono': '5598765431',
            'password': 'MiPasswordSegura123!',
            'fecha_nacimiento': date(1984, 2, 10),
            'rol': UserRole.ARRENDADOR,
            'url_ine': 'https://storage.parkshare.mx/ine.jpg',
            'url_csf': 'https://storage.parkshare.mx/csf.pdf',
            'url_propiedad': 'https://storage.parkshare.mx/prop.pdf',
        })
        suspend_account(reg['usuario'].id_usuario, self.admin, 'Documentos falsos.')

        with self.assertRaises(ConflictError):
            review_landlord_request(reg['documento'].id_documento, VerificationStatus.APROBADO)

    def test_suspend_account_invalidates_existing_jwt_and_blocks_login(self):
        reg = register_user({
            'nombre_completo': 'Pedro López',
            'correo': 'pedro@outlook.com',
            'telefono': '5511223344',
            'password': 'MiPasswordSegura123!',
            'fecha_nacimiento': date(1992, 6, 15),
            'rol': UserRole.ARRENDATARIO,
            'placas': 'XYZ-789-A',
            'modelo_vehiculo': 'Honda Civic',
        })
        user = reg['usuario']
        tokens = generate_tokens_for_user(user)

        request = self.factory.get('/dummy/', HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        auth = CustomJWTAuthentication()
        authed_user, _ = auth.authenticate(request)
        self.assertEqual(authed_user.id_usuario, user.id_usuario)

        refresh_req = self.factory.get('/dummy/', HTTP_AUTHORIZATION=f"Bearer {tokens['refresh']}")
        with self.assertRaises(AuthenticationFailed):
            auth.authenticate(refresh_req)

        suspension = suspend_account(user.id_usuario, self.admin, 'Daños reiterados.')
        self.assertEqual(suspension.estado, SuspensionStatus.ACTIVA)

        user.refresh_from_db()
        self.assertEqual(user.estado_cuenta, AccountStatus.SUSPENDIDA)
        self.assertEqual(user.token_version, 2)

        with self.assertRaises(AuthenticationFailed):
            auth.authenticate(request)

        with self.assertRaises(AccountSuspendedError):
            authenticate_user('pedro@outlook.com', 'MiPasswordSegura123!')

        with self.assertRaises(ConflictError):
            suspend_account(user.id_usuario, self.admin, 'Intento duplicado.')

    def test_suspend_self_or_other_admin_fails(self):
        other_admin = Usuario.objects.create_user(
            nombre_completo='Administrador Dos',
            correo='admin2@parkshare.mx',
            telefono='5500000002',
            password='AdminPassword123!',
            fecha_nacimiento=date(1990, 1, 1),
            rol=UserRole.ADMIN,
        )

        with self.assertRaises(DomainValidationError):
            suspend_account(self.admin.id_usuario, self.admin, 'Auto-suspensión')

        with self.assertRaises(DomainValidationError):
            suspend_account(other_admin.id_usuario, self.admin, 'Suspender otro admin')

    def test_permission_classes(self):
        pending_landlord = Usuario.objects.create_user(
            nombre_completo='Carlos Pendiente',
            correo='carlosp@gmail.com',
            telefono='5598765439',
            password='MiPasswordSegura123!',
            fecha_nacimiento=date(1985, 11, 20),
            rol=UserRole.ARRENDADOR,
        )
        active_tenant = Usuario.objects.create_user(
            nombre_completo='Ana Activa',
            correo='anaa@outlook.com',
            telefono='5512345670',
            password='MiPasswordSegura123!',
            fecha_nacimiento=date(1998, 5, 14),
            rol=UserRole.ARRENDATARIO,
            placas='ABC-123-D',
            modelo_vehiculo='Nissan Versa',
        )

        req_admin = self.factory.get('/')
        req_admin.user = self.admin
        req_landlord = self.factory.get('/')
        req_landlord.user = pending_landlord
        req_tenant = self.factory.get('/')
        req_tenant.user = active_tenant

        self.assertTrue(IsAdminRole().has_permission(req_admin, None))
        self.assertFalse(IsAdminRole().has_permission(req_tenant, None))

        self.assertTrue(IsActiveAccount().has_permission(req_tenant, None))
        self.assertFalse(IsActiveAccount().has_permission(req_landlord, None))

        self.assertTrue(IsActiveTenant().has_permission(req_tenant, None))
        self.assertFalse(IsActiveLandlord().has_permission(req_landlord, None))

        pending_landlord.estado_cuenta = AccountStatus.ACTIVA
        self.assertTrue(IsActiveLandlord().has_permission(req_landlord, None))
        self.assertFalse(IsActiveTenant().has_permission(req_landlord, None))
