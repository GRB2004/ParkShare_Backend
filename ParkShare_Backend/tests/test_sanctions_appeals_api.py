from datetime import date, datetime, time
from decimal import Decimal
import tempfile
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from ParkShare_Backend.models import (
    AccountStatus,
    AppealStatus,
    Cochera,
    ComplaintReasonCategory,
    ComplaintStatus,
    Disponibilidad,
    GarageStatus,
    Queja,
    Reserva,
    ReservationStatus,
    Sancion,
    SanctionStatus,
    UserRole,
    VehicleType,
    WeekdayChoice,
)

Usuario = get_user_model()
MEXICO_TZ = ZoneInfo('America/Mexico_City')


class SanctionsAndAppealsAPITests(APITestCase):
    def setUp(self):
        self.password = 'MiPasswordSegura123!'
        self.admin = Usuario.objects.create_user(
            nombre_completo='Admin Disciplina ParkShare',
            correo='admin.disc@parkshare.mx',
            telefono='5500000009',
            password=self.password,
            fecha_nacimiento=date(1984, 7, 7),
            rol=UserRole.ADMIN,
            estado_cuenta=AccountStatus.ACTIVA,
        )
        self.landlord = Usuario.objects.create_user(
            nombre_completo='María Elena Flores',
            correo='maria.flores@gmail.com',
            telefono='5598765432',
            password=self.password,
            fecha_nacimiento=date(1985, 11, 20),
            rol=UserRole.ARRENDADOR,
            estado_cuenta=AccountStatus.ACTIVA,
            cuenta_cobro='012180001234567890',
        )
        self.other_landlord = Usuario.objects.create_user(
            nombre_completo='Roberto Gómez Silva',
            correo='roberto.gomez@gmail.com',
            telefono='5587654321',
            password=self.password,
            fecha_nacimiento=date(1988, 3, 15),
            rol=UserRole.ARRENDADOR,
            estado_cuenta=AccountStatus.ACTIVA,
            cuenta_cobro='012180009876543210',
        )
        self.tenant = Usuario.objects.create_user(
            nombre_completo='Carlos Mendoza Ruiz',
            correo='carlos.mendoza@outlook.com',
            telefono='5512345678',
            password=self.password,
            fecha_nacimiento=date(1998, 5, 14),
            rol=UserRole.ARRENDATARIO,
            estado_cuenta=AccountStatus.ACTIVA,
            placas='ABC-123-D',
            modelo_vehiculo='Nissan Versa 2022',
        )

        self.garage = Cochera.objects.create(
            arrendador=self.landlord,
            nombre='Cochera Centro Norte',
            direccion='Av. Insurgentes Centro 1450',
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

        self.reservation = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=datetime(2026, 11, 25, 16, 0, tzinfo=MEXICO_TZ),
            fecha_hora_fin=datetime(2026, 11, 25, 18, 0, tzinfo=MEXICO_TZ),
            monto_base=Decimal('80.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.CONFIRMADA,
        )

    def _auth_as(self, email: str) -> None:
        res = self.client.post(
            '/auth/login',
            {'correo': email, 'password': self.password},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    def test_admin_quejas_bandeja_detail_and_resolution_workflow(self):
        c1 = Queja.objects.create(
            reserva=self.reservation,
            arrendatario=self.tenant,
            arrendador=self.landlord,
            motivo_categoria=ComplaintReasonCategory.NO_PUDE_ACCEDER,
            descripcion='Llegué a las 16:05 y el portón permaneció cerrado durante 25 minutos.',
        )
        c2 = Queja.objects.create(
            reserva=self.reservation,
            arrendatario=self.tenant,
            arrendador=self.landlord,
            motivo_categoria=ComplaintReasonCategory.TRATO_INADECUADO,
            descripcion='El arrendador contestó de forma grosera por teléfono.',
        )

        # Non-admin blocked -> 403
        for non_admin in (self.tenant.correo, self.landlord.correo):
            self._auth_as(non_admin)
            self.assertEqual(self.client.get('/admin/quejas').status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(
                self.client.get(f'/admin/quejas/{c1.id_queja}').status_code,
                status.HTTP_403_FORBIDDEN,
            )
            self.assertEqual(
                self.client.patch(
                    f'/admin/quejas/{c1.id_queja}', {'estado': 'DESCARTADA'}, format='json'
                ).status_code,
                status.HTTP_403_FORBIDDEN,
            )

        self._auth_as(self.admin.correo)

        # GET /admin/quejas with constant 3 queries (auth + quejas select_related + apelaciones select_related)
        with self.assertNumQueries(3):
            res_list = self.client.get('/admin/quejas')
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertEqual(res_list.data['totales']['quejas'], 2)
        self.assertEqual(res_list.data['totales']['apelaciones'], 0)

        # GET /admin/quejas/{id} detail
        res_detail = self.client.get(f'/admin/quejas/{c1.id_queja}')
        self.assertEqual(res_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(res_detail.data['id_queja'], c1.id_queja)
        self.assertEqual(res_detail.data['reserva']['cochera'], 'Cochera Centro Norte')
        self.assertEqual(res_detail.data['arrendador']['sanciones_vigentes'], 0)
        self.assertIsNone(res_detail.data['sancion'])

        # 404 for non-existent complaint
        self.assertEqual(self.client.get('/admin/quejas/999999').status_code, status.HTTP_404_NOT_FOUND)

        # PATCH /admin/quejas/{c1} -> DESCARTADA
        res_discard = self.client.patch(
            f'/admin/quejas/{c1.id_queja}',
            {'estado': 'DESCARTADA'},
            format='json',
        )
        self.assertEqual(res_discard.status_code, status.HTTP_200_OK)
        self.assertEqual(res_discard.data['estado'], ComplaintStatus.DESCARTADA)
        self.assertIsNone(res_discard.data['sancion'])

        # Double resolution -> 409 Conflict
        self.assertEqual(
            self.client.patch(
                f'/admin/quejas/{c1.id_queja}', {'estado': 'DESCARTADA'}, format='json'
            ).status_code,
            status.HTTP_409_CONFLICT,
        )

        # PATCH /admin/quejas/{c2} with REVISADA but missing motivo_sancion -> 400
        self.assertEqual(
            self.client.patch(
                f'/admin/quejas/{c2.id_queja}', {'estado': 'REVISADA'}, format='json'
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        # PATCH /admin/quejas/{c2} -> REVISADA with motivo_sancion creates VIGENTE sanction
        res_sanction = self.client.patch(
            f'/api/v1/admin/quejas/{c2.id_queja}',
            {
                'estado': 'REVISADA',
                'motivo_sancion': 'Se confirmó negativa injustificada de acceso al espacio reservado.',
            },
            format='json',
        )
        self.assertEqual(res_sanction.status_code, status.HTTP_200_OK)
        self.assertEqual(res_sanction.data['estado'], ComplaintStatus.REVISADA)
        self.assertEqual(res_sanction.data['sancion']['estado'], SanctionStatus.VIGENTE)

    def test_landlord_sanciones_and_apelaciones_full_lifecycle(self):
        c1 = Queja.objects.create(
            reserva=self.reservation,
            arrendatario=self.tenant,
            arrendador=self.landlord,
            motivo_categoria=ComplaintReasonCategory.NO_PUDE_ACCEDER,
            descripcion='Llegué puntual y el portón permaneció cerrado.',
            estado=ComplaintStatus.REVISADA,
        )
        s1 = Sancion.objects.create(
            queja=c1,
            arrendador=self.landlord,
            admin=self.admin,
            motivo='Incumplimiento de acceso puntual al arrendatario.',
            estado=SanctionStatus.VIGENTE,
        )
        c2 = Queja.objects.create(
            reserva=self.reservation,
            arrendatario=self.tenant,
            arrendador=self.landlord,
            motivo_categoria=ComplaintReasonCategory.OTRO,
            descripcion='Demora menor al abrir la puerta secundaria.',
            estado=ComplaintStatus.REVISADA,
        )
        s2 = Sancion.objects.create(
            queja=c2,
            arrendador=self.landlord,
            admin=self.admin,
            motivo='Demora en apertura de acceso.',
            estado=SanctionStatus.VIGENTE,
        )

        # Tenant cannot access /arrendador/sanciones -> 403
        self._auth_as(self.tenant.correo)
        self.assertEqual(
            self.client.get('/arrendador/sanciones').status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Other landlord cannot view or appeal s1 -> 403
        self._auth_as(self.other_landlord.correo)
        self.assertEqual(
            self.client.get(f'/arrendador/sanciones/{s1.id_sancion}').status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                f'/sanciones/{s1.id_sancion}/apelar',
                {'descripcion': 'Intento de apelación cruzada no permitida.'},
                format='json',
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Owning landlord lists sanctions in 2 constant queries (auth + select_related('apelacion'))
        self._auth_as(self.landlord.correo)
        with self.assertNumQueries(2):
            res_list = self.client.get('/arrendador/sanciones')
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertEqual(res_list.data['total'], 2)
        self.assertEqual(res_list.data['vigentes'], 2)
        self.assertTrue(res_list.data['sanciones'][0]['puede_apelar'])

        # Detail of s1
        res_det = self.client.get(f'/arrendador/sanciones/{s1.id_sancion}')
        self.assertEqual(res_det.status_code, status.HTTP_200_OK)
        self.assertEqual(res_det.data['id_sancion'], s1.id_sancion)
        self.assertEqual(res_det.data['queja_resumen']['reserva_id'], self.reservation.id_reserva)
        self.assertIsNone(res_det.data['apelacion'])

        # Invalid appeal payload (short description or url_evidencia > 255) -> 400
        for bad_appeal in (
            {'descripcion': 'Corto'},
            {
                'descripcion': 'Descripción suficientemente larga para prueba.',
                'url_evidencia': 'https://cdn.parkshare.mx/' + ('a' * 250),
            },
        ):
            self.assertEqual(
                self.client.post(
                    f'/sanciones/{s1.id_sancion}/apelar',
                    bad_appeal,
                    format='json',
                ).status_code,
                status.HTTP_400_BAD_REQUEST,
            )

        # Appeal s1 with url_evidencia -> 201 Created
        res_ap1 = self.client.post(
            f'/sanciones/{s1.id_sancion}/apelar',
            {
                'descripcion': 'Se fue la energía eléctrica de la colonia y el portón quedó bloqueado.',
                'url_evidencia': 'https://cdn.parkshare.mx/evidencias/reporte_cfe.jpg',
            },
            format='json',
        )
        self.assertEqual(res_ap1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_ap1.data['estado'], AppealStatus.EN_REVISION)
        ap1_id = res_ap1.data['id_apelacion']

        # Duplicate appeal on s1 -> 409 Conflict
        self.assertEqual(
            self.client.post(
                f'/sanciones/{s1.id_sancion}/apelar',
                {'descripcion': 'Segunda apelación sobre la misma sanción.'},
                format='json',
            ).status_code,
            status.HTTP_409_CONFLICT,
        )

        # Appeal s2 without url_evidencia (null/omitted) -> 201 Created
        res_ap2 = self.client.post(
            f'/api/v1/sanciones/{s2.id_sancion}/apelar',
            {'descripcion': 'El conductor llegó por la entrada posterior equivocada.'},
            format='json',
        )
        self.assertEqual(res_ap2.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(res_ap2.data['url_evidencia'])
        ap2_id = res_ap2.data['id_apelacion']

        # Admin resolves ap1 as ACEPTADA -> s1 becomes RETIRADA
        self._auth_as(self.admin.correo)
        res_resolve_1 = self.client.patch(
            f'/admin/apelaciones/{ap1_id}',
            {'estado': 'ACEPTADA'},
            format='json',
        )
        self.assertEqual(res_resolve_1.status_code, status.HTTP_200_OK)
        self.assertEqual(res_resolve_1.data['estado'], AppealStatus.ACEPTADA)
        self.assertEqual(res_resolve_1.data['sancion_estado'], SanctionStatus.RETIRADA)

        # Admin resolves ap2 as RECHAZADA -> s2 remains VIGENTE
        res_resolve_2 = self.client.patch(
            f'/api/v1/admin/apelaciones/{ap2_id}',
            {'estado': 'RECHAZADA'},
            format='json',
        )
        self.assertEqual(res_resolve_2.status_code, status.HTTP_200_OK)
        self.assertEqual(res_resolve_2.data['estado'], AppealStatus.RECHAZADA)
        self.assertEqual(res_resolve_2.data['sancion_estado'], SanctionStatus.VIGENTE)

        # Double resolution of ap1 -> 409 Conflict
        self.assertEqual(
            self.client.patch(
                f'/admin/apelaciones/{ap1_id}', {'estado': 'RECHAZADA'}, format='json'
            ).status_code,
            status.HTTP_409_CONFLICT,
        )

    def test_appeal_attachment_upload_and_admin_inspection_serves_saved_evidence(self):
        c1 = Queja.objects.create(
            reserva=self.reservation,
            arrendatario=self.tenant,
            arrendador=self.landlord,
            motivo_categoria=ComplaintReasonCategory.NO_PUDE_ACCEDER,
            descripcion='No se abrió el portón automático al llegar a la cochera.',
            estado=ComplaintStatus.REVISADA,
        )
        s1 = Sancion.objects.create(
            queja=c1,
            arrendador=self.landlord,
            admin=self.admin,
            motivo='Falta de acceso reportada.',
            estado=SanctionStatus.VIGENTE,
        )
        c2 = Queja.objects.create(
            reserva=self.reservation,
            arrendatario=self.tenant,
            arrendador=self.landlord,
            motivo_categoria=ComplaintReasonCategory.OTRO,
            descripcion='Incidencia en el interfón secundario.',
            estado=ComplaintStatus.REVISADA,
        )
        s2 = Sancion.objects.create(
            queja=c2,
            arrendador=self.landlord,
            admin=self.admin,
            motivo='Incidencia de acceso secundario.',
            estado=SanctionStatus.VIGENTE,
        )

        pdf_bytes = b'%PDF-1.4\n%REPORTE-CFE-EVIDENCIA\n%%EOF'
        png_bytes = b'\x89PNG\r\n\x1a\nEVIDENCIA-PORTON'

        with tempfile.TemporaryDirectory() as tmp_media:
            with override_settings(MEDIA_ROOT=tmp_media, MEDIA_URL='/media/'):
                self._auth_as(self.landlord.correo)

                # Flow 1: 2-step upload via POST /archivos/subir + POST /sanciones/{id}/apelar
                upload_res = self.client.post(
                    '/archivos/subir',
                    {
                        'carpeta': 'evidencias',
                        'archivo': SimpleUploadedFile(
                            'Reporte CFE Colonia.pdf',
                            pdf_bytes,
                            content_type='application/pdf',
                        ),
                    },
                    format='multipart',
                )
                self.assertEqual(upload_res.status_code, status.HTTP_201_CREATED)
                self.assertTrue(upload_res.data['url'].startswith('/media/evidencias/'))
                self.assertTrue(upload_res.data['url'].endswith('.pdf'))

                ap1_res = self.client.post(
                    f'/sanciones/{s1.id_sancion}/apelar',
                    {
                        'descripcion': 'Adjunto reporte oficial de corte eléctrico de CFE en la cuadra.',
                        'url_evidencia': upload_res.data['url'],
                    },
                    format='json',
                )
                self.assertEqual(ap1_res.status_code, status.HTTP_201_CREATED)
                self.assertEqual(ap1_res.data['url_evidencia'], upload_res.data['url'])
                ap1_id = ap1_res.data['id_apelacion']

                # Flow 2: Direct multipart file upload on POST /sanciones/{id}/apelar
                ap2_res = self.client.post(
                    f'/api/v1/sanciones/{s2.id_sancion}/apelar',
                    {
                        'descripcion': 'Adjunto fotografía de la bitácora del interfón funcionando.',
                        'archivo_evidencia': SimpleUploadedFile(
                            'bitacora_interfon.png',
                            png_bytes,
                            content_type='image/png',
                        ),
                    },
                    format='multipart',
                )
                self.assertEqual(ap2_res.status_code, status.HTTP_201_CREATED)
                self.assertTrue(ap2_res.data['url_evidencia'].startswith('/media/evidencias/'))
                self.assertTrue(ap2_res.data['url_evidencia'].endswith('.png'))
                ap2_id = ap2_res.data['id_apelacion']

                # Admin inspects inbox (still 3 constant queries!) and individual appeal detail + downloads evidence
                self._auth_as(self.admin.correo)
                with self.assertNumQueries(3):
                    inbox_res = self.client.get('/admin/quejas')
                self.assertEqual(inbox_res.status_code, status.HTTP_200_OK)
                self.assertEqual(inbox_res.data['totales']['apelaciones'], 2)
                inbox_urls = {
                    a['id_apelacion']: a['url_evidencia']
                    for a in inbox_res.data['apelaciones_en_revision']
                }
                self.assertEqual(inbox_urls[ap1_id], upload_res.data['url'])
                self.assertEqual(inbox_urls[ap2_id], ap2_res.data['url_evidencia'])

                ap1_detail = self.client.get(f'/admin/apelaciones/{ap1_id}')
                self.assertEqual(ap1_detail.status_code, status.HTTP_200_OK)
                self.assertEqual(ap1_detail.data['url_evidencia'], upload_res.data['url'])
                self.assertIn('archivo_evidencia_url', ap1_detail.data)
                self.assertEqual(ap1_detail.data['arrendador']['correo'], self.landlord.correo)

                dl_pdf = self.client.get(ap1_detail.data['url_evidencia'])
                self.assertEqual(dl_pdf.status_code, status.HTTP_200_OK)
                self.assertEqual(b''.join(dl_pdf.streaming_content), pdf_bytes)
                self.assertIn('application/pdf', dl_pdf['Content-Type'])

                dl_png = self.client.get(ap2_res.data['url_evidencia'])
                self.assertEqual(dl_png.status_code, status.HTTP_200_OK)
                self.assertEqual(b''.join(dl_png.streaming_content), png_bytes)
                self.assertIn('image/png', dl_png['Content-Type'])
