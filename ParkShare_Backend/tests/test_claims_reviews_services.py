from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Apelacion,
    AppealStatus,
    Cochera,
    ComplaintReasonCategory,
    ComplaintStatus,
    Disponibilidad,
    GarageStatus,
    Queja,
    Resena,
    Reserva,
    ReservationStatus,
    Sancion,
    SanctionStatus,
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
from ParkShare_Backend.services.claim_sanction_service import (
    create_complaint,
    get_admin_complaint_detail,
    get_landlord_sanction_detail,
    list_admin_claims_and_appeals,
    list_landlord_sanctions,
    resolve_appeal,
    resolve_complaint,
    submit_appeal,
)
from ParkShare_Backend.services.review_service import create_review

MEXICO_TZ = ZoneInfo('America/Mexico_City')


def _create_user(email: str, role: str, status: str = AccountStatus.ACTIVA) -> Usuario:
    extra = {}
    if role == UserRole.ARRENDADOR:
        extra = {'cuenta_cobro': '012180001234567890'}
    elif role == UserRole.ARRENDATARIO:
        extra = {'placas': 'ABC-123-D', 'modelo_vehiculo': 'Nissan Versa 2022'}
    return Usuario.objects.create_user(
        nombre_completo='Usuario Servicios Modulo Seis',
        correo=email,
        telefono='5512345678',
        password='MiPasswordSegura123!',
        fecha_nacimiento=date(1990, 5, 10),
        rol=role,
        estado_cuenta=status,
        **extra,
    )


def _create_garage(landlord: Usuario) -> Cochera:
    garage = Cochera.objects.create(
        arrendador=landlord,
        nombre='Cochera Servicios Centro',
        direccion='Av. Reforma 222',
        latitud=Decimal('19.42700000'),
        longitud=Decimal('-99.16700000'),
        metros_cuadrados=Decimal('32.00'),
        espacios_totales=2,
        tarifa_hora=Decimal('40.00'),
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


class ClaimsAndReviewsServicesTests(TestCase):
    def setUp(self):
        self.admin = _create_user('admin.srv6@parkshare.mx', UserRole.ADMIN)
        self.landlord = _create_user('host.srv6@gmail.com', UserRole.ARRENDADOR)
        self.other_landlord = _create_user('host2.srv6@gmail.com', UserRole.ARRENDADOR)
        self.tenant = _create_user('tenant.srv6@outlook.com', UserRole.ARRENDATARIO)
        self.other_tenant = _create_user('tenant2.srv6@outlook.com', UserRole.ARRENDATARIO)
        self.garage = _create_garage(self.landlord)
        self.start_dt = datetime(2026, 11, 26, 10, 0, tzinfo=MEXICO_TZ)
        self.end_dt = datetime(2026, 11, 26, 12, 0, tzinfo=MEXICO_TZ)

    def _make_reservation(self, estado: str = ReservationStatus.FINALIZADA) -> Reserva:
        return Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.end_dt,
            monto_base=Decimal('80.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=estado,
        )

    def test_create_review_bilateral_and_guards_cu09(self):
        r_active = self._make_reservation(ReservationStatus.CONFIRMADA)
        with self.assertRaises(ConflictError):
            create_review(r_active.id_reserva, self.tenant, calificacion=5)

        r_done = self._make_reservation(ReservationStatus.FINALIZADA)

        # Unrelated user or admin cannot review
        for outsider in (self.other_tenant, self.other_landlord, self.admin):
            with self.assertRaises(PermissionDeniedError):
                create_review(r_done.id_reserva, outsider, calificacion=5)

        # Invalid rating values
        for bad_rating in (0, 6, -1, True, 'cinco'):
            with self.assertRaises(DomainValidationError):
                create_review(r_done.id_reserva, self.tenant, calificacion=bad_rating)

        # 1. Tenant reviews landlord
        rev_tenant = create_review(
            r_done.id_reserva,
            self.tenant,
            calificacion=5,
            etiquetas='Puntual,Buen trato',
            comentario='Excelente cochera.',
        )
        self.assertEqual(rev_tenant.autor_id, self.tenant.id_usuario)
        self.assertEqual(rev_tenant.destinatario_id, self.landlord.id_usuario)

        # Duplicate review by same tenant -> ConflictError
        with self.assertRaises(ConflictError):
            create_review(r_done.id_reserva, self.tenant, calificacion=4)

        # 2. Landlord reviews tenant
        rev_host = create_review(
            r_done.id_reserva,
            self.landlord,
            calificacion=4,
        )
        self.assertEqual(rev_host.autor_id, self.landlord.id_usuario)
        self.assertEqual(rev_host.destinatario_id, self.tenant.id_usuario)
        self.assertEqual(Resena.objects.filter(reserva=r_done).count(), 2)

    def test_create_and_resolve_complaint_discard_vs_sanction_cu12_cu13(self):
        # Invalid reservation states for complaints
        for invalid_state in (
            ReservationStatus.SOLICITADA,
            ReservationStatus.CANCELADA,
            ReservationStatus.EXPIRADA,
        ):
            r_bad = self._make_reservation(invalid_state)
            with self.assertRaises(ConflictError):
                create_complaint(
                    r_bad.id_reserva,
                    self.tenant,
                    ComplaintReasonCategory.NO_PUDE_ACCEDER,
                    'Llegué puntual pero el portón nunca se abrió.',
                )

        # Valid reservation states: CONFIRMADA, EN_USO, FINALIZADA
        r_valid = self._make_reservation(ReservationStatus.CONFIRMADA)

        # Non-owner or landlord cannot file complaint
        for bad_actor in (self.other_tenant, self.landlord):
            with self.assertRaises(PermissionDeniedError):
                create_complaint(
                    r_valid.id_reserva,
                    bad_actor,
                    ComplaintReasonCategory.NO_PUDE_ACCEDER,
                    'Llegué puntual pero el portón nunca se abrió.',
                )

        # Short description (<10 chars) or invalid category -> DomainValidationError
        with self.assertRaises(DomainValidationError):
            create_complaint(
                r_valid.id_reserva,
                self.tenant,
                ComplaintReasonCategory.NO_PUDE_ACCEDER,
                'Corto',
            )

        c1 = create_complaint(
            r_valid.id_reserva,
            self.tenant,
            ComplaintReasonCategory.NO_PUDE_ACCEDER,
            'Llegué a las 10:00 y el portón permaneció cerrado durante 25 minutos.',
        )
        self.assertEqual(c1.estado, ComplaintStatus.PENDIENTE)
        self.assertEqual(c1.arrendador_id, self.landlord.id_usuario)

        # Discard c1 -> no sanction created
        res_discard = resolve_complaint(c1.id_queja, self.admin, ComplaintStatus.DESCARTADA)
        self.assertEqual(res_discard['estado'], ComplaintStatus.DESCARTADA)
        self.assertIsNone(res_discard['sancion'])

        # Re-resolving already discarded complaint -> ConflictError
        with self.assertRaises(ConflictError):
            resolve_complaint(c1.id_queja, self.admin, ComplaintStatus.DESCARTADA)

        # Create second complaint and resolve with REVISADA -> creates Sancion(VIGENTE)
        c2 = create_complaint(
            r_valid.id_reserva,
            self.tenant,
            ComplaintReasonCategory.TRATO_INADECUADO,
            'El propietario respondió de manera agresiva cuando solicité acceso.',
        )
        # Missing motivo_sancion when REVISADA -> DomainValidationError
        with self.assertRaises(DomainValidationError):
            resolve_complaint(c2.id_queja, self.admin, ComplaintStatus.REVISADA, motivo_sancion='')

        res_sanction = resolve_complaint(
            c2.id_queja,
            self.admin,
            ComplaintStatus.REVISADA,
            motivo_sancion='Se confirmó trato inadecuado e incumplimiento de acceso.',
        )
        self.assertEqual(res_sanction['estado'], ComplaintStatus.REVISADA)
        self.assertIsNotNone(res_sanction['sancion'])
        self.assertEqual(res_sanction['sancion']['estado'], SanctionStatus.VIGENTE)

    def test_submit_and_resolve_appeal_accepted_vs_rejected_cu13(self):
        r_done = self._make_reservation(ReservationStatus.FINALIZADA)
        c1 = create_complaint(
            r_done.id_reserva,
            self.tenant,
            ComplaintReasonCategory.NO_PUDE_ACCEDER,
            'No pude ingresar a la cochera en el horario confirmado.',
        )
        res1 = resolve_complaint(
            c1.id_queja,
            self.admin,
            ComplaintStatus.REVISADA,
            motivo_sancion='Negativa de acceso confirmada.',
        )
        s1_id = res1['sancion']['id_sancion']

        # Other landlord cannot appeal s1 -> PermissionDeniedError
        with self.assertRaises(PermissionDeniedError):
            submit_appeal(
                s1_id,
                self.other_landlord,
                descripcion='Intento de apelación por otro arrendador.',
            )

        # Landlord submits appeal on s1
        appeal_res = submit_appeal(
            s1_id,
            self.landlord,
            descripcion='Hubo un corte general de energía eléctrica en la cuadra.',
            url_evidencia='https://cdn.parkshare.mx/evidencias/cfe.jpg',
        )
        self.assertEqual(appeal_res['estado'], AppealStatus.EN_REVISION)

        # Duplicate appeal on same sanction -> ConflictError
        with self.assertRaises(ConflictError):
            submit_appeal(
                s1_id,
                self.landlord,
                descripcion='Segundo intento de apelación duplicado.',
            )

        # Admin accepts appeal -> sanction becomes RETIRADA
        resolved_accept = resolve_appeal(
            appeal_res['id_apelacion'],
            self.admin,
            AppealStatus.ACEPTADA,
        )
        self.assertEqual(resolved_accept['estado'], AppealStatus.ACEPTADA)
        self.assertEqual(resolved_accept['sancion_estado'], SanctionStatus.RETIRADA)

        # Re-resolving already accepted appeal -> ConflictError
        with self.assertRaises(ConflictError):
            resolve_appeal(appeal_res['id_apelacion'], self.admin, AppealStatus.RECHAZADA)

        # Appealing a RETIRADA sanction -> ConflictError
        s1 = Sancion.objects.get(pk=s1_id)
        self.assertEqual(s1.estado, SanctionStatus.RETIRADA)
        with self.assertRaises(ConflictError):
            submit_appeal(
                s1_id,
                self.landlord,
                descripcion='Intento de apelar una sanción ya retirada.',
            )
