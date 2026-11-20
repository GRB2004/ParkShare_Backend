from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from ParkShare_Backend.models import (
    AccountStatus,
    DocumentoArrendador,
    SuspensionCuenta,
    SuspensionStatus,
    UserRole,
    Usuario,
    VerificationStatus,
)


def _today_mexico():
    return timezone.now().astimezone(ZoneInfo('America/Mexico_City')).date()


def _eighteen_years_ago_today():
    today = _today_mexico()
    try:
        return today.replace(year=today.year - 18)
    except ValueError:
        return today.replace(year=today.year - 18, month=2, day=28)


class UsuarioModelTests(TestCase):
    def test_auth_user_model_is_usuario_and_create_superuser(self):
        self.assertIs(get_user_model(), Usuario)
        superuser = Usuario.objects.create_superuser(
            correo='superadmin@parkshare.mx',
            nombre_completo='Super Administrador',
            telefono='5500000099',
            fecha_nacimiento=date(1990, 1, 1),
            password='SuperPassword123!',
        )
        self.assertTrue(superuser.is_superuser)
        self.assertTrue(superuser.is_staff)
        self.assertEqual(superuser.rol, UserRole.ADMIN)
        self.assertEqual(superuser.estado_cuenta, AccountStatus.ACTIVA)
        self.assertTrue(superuser.check_password('SuperPassword123!'))

    def test_create_tenant_defaults_and_password_hash(self):
        user = Usuario.objects.create_user(
            nombre_completo='Ana Sofía Martínez López',
            correo='Ana.Martinez@Outlook.com',
            telefono='5512345678',
            password='MiPasswordSegura123!',
            fecha_nacimiento=date(1998, 5, 14),
            rol=UserRole.ARRENDATARIO,
            placas='ABC-123-D',
            modelo_vehiculo='Nissan Versa 2022',
        )

        self.assertEqual(Usuario._meta.db_table, 'usuario')
        self.assertIsNotNone(user.id_usuario)
        self.assertEqual(user.correo, 'ana.martinez@outlook.com')
        self.assertEqual(user.estado_cuenta, AccountStatus.ACTIVA)
        self.assertEqual(user.token_version, 1)
        self.assertFalse(user.is_staff)
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_authenticated)
        self.assertFalse(user.is_anonymous)
        self.assertNotEqual(user.password_hash, 'MiPasswordSegura123!')
        self.assertEqual(user.password, user.password_hash)
        self.assertTrue(user.check_password('MiPasswordSegura123!'))
        self.assertFalse(user.check_password('WrongPass'))

    def test_create_landlord_defaults_to_pending_and_requires_gmail(self):
        landlord = Usuario.objects.create_user(
            nombre_completo='Carlos Eduardo Ramírez Pérez',
            correo='Carlos.Ramirez@GMAIL.COM',
            telefono='+525598765432',
            password='MiPasswordSegura123!',
            fecha_nacimiento=date(1985, 11, 20),
            rol=UserRole.ARRENDADOR,
            cuenta_cobro='012180001234567890',
        )

        self.assertEqual(landlord.correo, 'carlos.ramirez@gmail.com')
        self.assertEqual(landlord.estado_cuenta, AccountStatus.PENDIENTE)
        self.assertTrue(landlord.is_active)
        self.assertFalse(landlord.is_staff)

    def test_create_admin_user_sets_is_staff_true(self):
        admin_user = Usuario.objects.create_user(
            nombre_completo='Administrador Sistema',
            correo='admin@parkshare.mx',
            telefono='5500000001',
            password='AdminPassword123!',
            fecha_nacimiento=date(1990, 1, 1),
            rol=UserRole.ADMIN,
        )

        self.assertEqual(admin_user.estado_cuenta, AccountStatus.ACTIVA)
        self.assertTrue(admin_user.is_staff)
        self.assertTrue(admin_user.is_active)

    def test_landlord_non_gmail_fails_validation(self):
        user = Usuario(
            nombre_completo='Carlos Ramírez',
            correo='carlos@hotmail.com',
            telefono='5598765432',
            fecha_nacimiento=date(1985, 11, 20),
            rol=UserRole.ARRENDADOR,
        )
        user.set_password('MiPasswordSegura123!')

        with self.assertRaises(ValidationError) as ctx:
            user.full_clean()
        self.assertIn('correo', ctx.exception.message_dict)

    def test_duplicate_email_case_insensitive_raises_integrity_error(self):
        Usuario.objects.create_user(
            nombre_completo='Ana Martínez',
            correo='ana@outlook.com',
            telefono='5512345678',
            password='MiPasswordSegura123!',
            fecha_nacimiento=date(1998, 5, 14),
            rol=UserRole.ARRENDATARIO,
            placas='ABC-123-D',
            modelo_vehiculo='Nissan Versa 2022',
        )

        with self.assertRaises(IntegrityError):
            Usuario.objects.create_user(
                nombre_completo='Otra Ana',
                correo='ANA@OUTLOOK.COM',
                telefono='5512345679',
                password='MiPasswordSegura123!',
                fecha_nacimiento=date(1998, 5, 14),
                rol=UserRole.ARRENDATARIO,
                placas='XYZ-999-A',
                modelo_vehiculo='Mazda 3',
            )

    def test_age_exactly_eighteen_today_passes_and_underage_fails(self):
        exact_18_dob = _eighteen_years_ago_today()
        adult = Usuario(
            nombre_completo='Luis Gómez',
            correo='luis@outlook.com',
            telefono='5511112222',
            fecha_nacimiento=exact_18_dob,
            rol=UserRole.ARRENDATARIO,
            placas='DEF-456-G',
            modelo_vehiculo='VW Jetta',
        )
        adult.set_password('MiPasswordSegura123!')
        adult.full_clean()

        underage_dob = exact_18_dob + timedelta(days=1)
        minor = Usuario(
            nombre_completo='Luis Menor',
            correo='luismenor@outlook.com',
            telefono='5511112223',
            fecha_nacimiento=underage_dob,
            rol=UserRole.ARRENDATARIO,
            placas='DEF-456-H',
            modelo_vehiculo='VW Jetta',
        )
        minor.set_password('MiPasswordSegura123!')

        with self.assertRaises(ValidationError) as ctx:
            minor.full_clean()
        self.assertIn('fecha_nacimiento', ctx.exception.message_dict)

    def test_non_alphabetic_name_and_invalid_phone_fail(self):
        user = Usuario(
            nombre_completo='Juan Pérez 123',
            correo='juan@outlook.com',
            telefono='12345',
            fecha_nacimiento=date(1995, 3, 10),
            rol=UserRole.ARRENDATARIO,
            placas='AAA-111-B',
            modelo_vehiculo='Kia Rio',
        )
        user.set_password('MiPasswordSegura123!')

        with self.assertRaises(ValidationError) as ctx:
            user.full_clean()
        self.assertIn('nombre_completo', ctx.exception.message_dict)
        self.assertIn('telefono', ctx.exception.message_dict)

    def test_tenant_requires_vehicle_plates_and_model(self):
        user = Usuario(
            nombre_completo='Sofía Torres',
            correo='sofia@outlook.com',
            telefono='5522334455',
            fecha_nacimiento=date(1996, 8, 20),
            rol=UserRole.ARRENDATARIO,
        )
        user.set_password('MiPasswordSegura123!')

        with self.assertRaises(ValidationError) as ctx:
            user.full_clean()
        self.assertIn('placas', ctx.exception.message_dict)
        self.assertIn('modelo_vehiculo', ctx.exception.message_dict)


class DocumentoArrendadorModelTests(TestCase):
    def setUp(self):
        self.landlord = Usuario.objects.create_user(
            nombre_completo='Carlos Ramírez',
            correo='carlos@gmail.com',
            telefono='5598765432',
            password='MiPasswordSegura123!',
            fecha_nacimiento=date(1985, 11, 20),
            rol=UserRole.ARRENDADOR,
        )

    def test_create_documento_arrendador_defaults_and_valid_extensions(self):
        doc = DocumentoArrendador.objects.create(
            arrendador=self.landlord,
            url_ine='https://storage.parkshare.mx/docs/carlos_ine.jpg',
            url_csf='https://storage.parkshare.mx/docs/carlos_csf.pdf',
            url_propiedad='https://storage.parkshare.mx/docs/carlos_escritura.pdf',
        )
        doc.full_clean()

        self.assertEqual(DocumentoArrendador._meta.db_table, 'documento_arrendador')
        self.assertEqual(doc.estado_verificacion, VerificationStatus.PENDIENTE)
        self.assertIsNone(doc.motivo_rechazo)

    def test_invalid_document_extensions_fail_validation(self):
        doc = DocumentoArrendador(
            arrendador=self.landlord,
            url_ine='https://storage.parkshare.mx/docs/carlos_ine.exe',
            url_csf='https://storage.parkshare.mx/docs/carlos_csf.jpg',
            url_propiedad='https://storage.parkshare.mx/docs/carlos_escritura.png',
        )

        with self.assertRaises(ValidationError) as ctx:
            doc.full_clean()
        self.assertIn('url_ine', ctx.exception.message_dict)
        self.assertIn('url_csf', ctx.exception.message_dict)
        self.assertIn('url_propiedad', ctx.exception.message_dict)

    def test_rejected_document_requires_motivo_rechazo(self):
        doc = DocumentoArrendador(
            arrendador=self.landlord,
            url_ine='https://storage.parkshare.mx/docs/carlos_ine.pdf',
            url_csf='https://storage.parkshare.mx/docs/carlos_csf.pdf',
            url_propiedad='https://storage.parkshare.mx/docs/carlos_escritura.pdf',
            estado_verificacion=VerificationStatus.RECHAZADO,
            motivo_rechazo='',
        )

        with self.assertRaises(ValidationError) as ctx:
            doc.full_clean()
        self.assertIn('motivo_rechazo', ctx.exception.message_dict)


class SuspensionCuentaModelTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user(
            nombre_completo='Ana Martínez',
            correo='ana@outlook.com',
            telefono='5512345678',
            password='MiPasswordSegura123!',
            fecha_nacimiento=date(1998, 5, 14),
            rol=UserRole.ARRENDATARIO,
            placas='ABC-123-D',
            modelo_vehiculo='Nissan Versa 2022',
        )
        self.admin = Usuario.objects.create_user(
            nombre_completo='Admin Uno',
            correo='admin@parkshare.mx',
            telefono='5500000001',
            password='AdminPassword123!',
            fecha_nacimiento=date(1990, 1, 1),
            rol=UserRole.ADMIN,
        )

    def test_create_suspension_cuenta_defaults(self):
        suspension = SuspensionCuenta.objects.create(
            usuario=self.user,
            admin=self.admin,
            motivo='Reincidencia en daños reportados por arrendadores.',
        )
        suspension.full_clean()

        self.assertEqual(SuspensionCuenta._meta.db_table, 'suspension_cuenta')
        self.assertEqual(suspension.estado, SuspensionStatus.ACTIVA)
        self.assertIsNotNone(suspension.fecha_suspension)

    def test_empty_motivo_fails_validation(self):
        suspension = SuspensionCuenta(
            usuario=self.user,
            admin=self.admin,
            motivo='   ',
        )

        with self.assertRaises(ValidationError) as ctx:
            suspension.full_clean()
        self.assertIn('motivo', ctx.exception.message_dict)
