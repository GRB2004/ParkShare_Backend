from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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

MEXICO_TZ = ZoneInfo('America/Mexico_City')


def _create_user(email: str, role: str, status: str = AccountStatus.ACTIVA) -> Usuario:
    extra = {}
    if role == UserRole.ARRENDADOR:
        extra = {'cuenta_cobro': '012180001234567890'}
    elif role == UserRole.ARRENDATARIO:
        extra = {'placas': 'ABC-123-D', 'modelo_vehiculo': 'Nissan Versa 2022'}
    return Usuario.objects.create_user(
        nombre_completo='Usuario Modelos Modulo Seis',
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
        nombre='Cochera Reseñas Centro',
        direccion='Av. Juárez 120',
        latitud=Decimal('19.43260000'),
        longitud=Decimal('-99.13320000'),
        metros_cuadrados=Decimal('30.00'),
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


class ClaimsAndReviewsModelsTests(TestCase):
    def setUp(self):
        self.admin = _create_user('admin.mod6@parkshare.mx', UserRole.ADMIN)
        self.landlord = _create_user('host.mod6@gmail.com', UserRole.ARRENDADOR)
        self.tenant = _create_user('tenant.mod6@outlook.com', UserRole.ARRENDATARIO)
        self.garage = _create_garage(self.landlord)
        self.start_dt = datetime(2026, 11, 26, 10, 0, tzinfo=MEXICO_TZ)
        self.end_dt = datetime(2026, 11, 26, 12, 0, tzinfo=MEXICO_TZ)
        self.reservation = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=self.start_dt,
            fecha_hora_fin=self.end_dt,
            monto_base=Decimal('80.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.FINALIZADA,
        )

    def test_resena_model_creation_constraints_and_uniqueness(self):
        self.assertEqual(Resena._meta.db_table, 'resena')
        review = Resena.objects.create(
            reserva=self.reservation,
            autor=self.tenant,
            destinatario=self.landlord,
            calificacion=5,
            etiquetas='Puntual,Respetó el espacio,Buen trato',
            comentario='Excelente servicio.',
        )
        review.full_clean()
        self.assertIsNotNone(review.id_resena)
        self.assertIsNotNone(review.fecha)

        # Out-of-range calificacion (0 or 6) rejected
        for bad_score in (0, 6):
            bad_review = Resena(
                reserva=self.reservation,
                autor=self.landlord,
                destinatario=self.tenant,
                calificacion=bad_score,
            )
            with self.assertRaises(ValidationError):
                bad_review.full_clean()

        # Self-review (autor == destinatario) rejected
        self_review = Resena(
            reserva=self.reservation,
            autor=self.tenant,
            destinatario=self.tenant,
            calificacion=4,
        )
        with self.assertRaises(ValidationError):
            self_review.full_clean()

        # Duplicate (reserva, autor) raises IntegrityError
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Resena.objects.create(
                    reserva=self.reservation,
                    autor=self.tenant,
                    destinatario=self.landlord,
                    calificacion=4,
                )

    def test_queja_model_defaults_and_enum_validation(self):
        self.assertEqual(Queja._meta.db_table, 'queja')
        complaint = Queja.objects.create(
            reserva=self.reservation,
            arrendatario=self.tenant,
            arrendador=self.landlord,
            motivo_categoria=ComplaintReasonCategory.NO_PUDE_ACCEDER,
            descripcion='Llegué puntual y el portón permaneció cerrado más de 25 minutos.',
        )
        complaint.full_clean()
        self.assertIsNotNone(complaint.id_queja)
        self.assertEqual(complaint.estado, ComplaintStatus.PENDIENTE)
        self.assertIsNotNone(complaint.fecha_creacion)

        bad_complaint = Queja(
            reserva=self.reservation,
            arrendatario=self.tenant,
            arrendador=self.landlord,
            motivo_categoria='MOTIVO_INEXISTENTE',
            descripcion='Descripción válida para prueba de enum.',
        )
        with self.assertRaises(ValidationError):
            bad_complaint.full_clean()

    def test_sancion_and_apelacion_one_to_one_constraints(self):
        self.assertEqual(Sancion._meta.db_table, 'sancion')
        self.assertEqual(Apelacion._meta.db_table, 'apelacion')

        complaint = Queja.objects.create(
            reserva=self.reservation,
            arrendatario=self.tenant,
            arrendador=self.landlord,
            motivo_categoria=ComplaintReasonCategory.TRATO_INADECUADO,
            descripcion='El arrendador tuvo una actitud hostil al momento de abrir la cochera.',
            estado=ComplaintStatus.REVISADA,
        )
        sanction = Sancion.objects.create(
            queja=complaint,
            arrendador=self.landlord,
            admin=self.admin,
            motivo='Conducta inapropiada confirmada por administración.',
        )
        sanction.full_clean()
        self.assertEqual(sanction.estado, SanctionStatus.VIGENTE)

        # Duplicate sanction for same complaint raises IntegrityError
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Sancion.objects.create(
                    queja=complaint,
                    arrendador=self.landlord,
                    admin=self.admin,
                    motivo='Segunda sanción duplicada.',
                )

        appeal = Apelacion.objects.create(
            sancion=sanction,
            descripcion='Adjunto evidencia de que el acceso se abrió en tiempo y forma.',
            url_evidencia=None,
        )
        appeal.full_clean()
        self.assertEqual(appeal.estado, AppealStatus.EN_REVISION)

        # Duplicate appeal for same sanction raises IntegrityError
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Apelacion.objects.create(
                    sancion=sanction,
                    descripcion='Segunda apelación duplicada.',
                )
