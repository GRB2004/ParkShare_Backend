from datetime import date, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from ParkShare_Backend.models import (
    AccountStatus,
    Cochera,
    Disponibilidad,
    FotoCochera,
    GarageStatus,
    UserRole,
    Usuario,
    VehicleType,
    WeekdayChoice,
)


def _create_landlord(email: str = 'arrendador@gmail.com') -> Usuario:
    return Usuario.objects.create_user(
        nombre_completo='Carlos Eduardo Ramírez Pérez',
        correo=email,
        telefono='5598765432',
        password='MiPasswordSegura123!',
        fecha_nacimiento=date(1985, 11, 20),
        rol=UserRole.ARRENDADOR,
        estado_cuenta=AccountStatus.ACTIVA,
        cuenta_cobro='012180001234567890',
    )


class CocheraModelTests(TestCase):
    def setUp(self):
        self.landlord = _create_landlord()

    def test_create_cochera_defaults_and_table_metadata(self):
        garage = Cochera(
            arrendador=self.landlord,
            nombre='Cochera Los Pinos',
            direccion='Av. Insurgentes Sur 1450, Col. Actipan, C.P. 03230',
            latitud=Decimal('19.37124500'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('35.50'),
            espacios_totales=2,
            tarifa_hora=Decimal('45.00'),
        )
        garage.full_clean()
        garage.save()

        self.assertEqual(Cochera._meta.db_table, 'cochera')
        self.assertEqual(garage.tipo_vehiculo, VehicleType.AMBOS)
        self.assertEqual(garage.estado_cochera, GarageStatus.PENDIENTE)
        self.assertIsNotNone(garage.id_cochera)

    def test_cochera_validates_coordinate_boundaries(self):
        boundary_garage = Cochera(
            arrendador=self.landlord,
            nombre='Cochera Extremo',
            direccion='Calle Sur 100',
            latitud=Decimal('-90.00000000'),
            longitud=Decimal('180.00000000'),
            metros_cuadrados=Decimal('20.00'),
            espacios_totales=1,
            tarifa_hora=Decimal('30.00'),
        )
        boundary_garage.full_clean()

        invalid_lat_high = Cochera(
            arrendador=self.landlord,
            nombre='Cochera Latitud Alta',
            direccion='Calle Sur 100',
            latitud=Decimal('95.00000000'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('20.00'),
            espacios_totales=1,
            tarifa_hora=Decimal('30.00'),
        )
        with self.assertRaises(ValidationError) as ctx_lat_high:
            invalid_lat_high.full_clean()
        self.assertIn('latitud', ctx_lat_high.exception.message_dict)

        invalid_lat_low = Cochera(
            arrendador=self.landlord,
            nombre='Cochera Latitud Baja',
            direccion='Calle Sur 100',
            latitud=Decimal('-90.00000001'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('20.00'),
            espacios_totales=1,
            tarifa_hora=Decimal('30.00'),
        )
        with self.assertRaises(ValidationError) as ctx_lat_low:
            invalid_lat_low.full_clean()
        self.assertIn('latitud', ctx_lat_low.exception.message_dict)

        invalid_lng = Cochera(
            arrendador=self.landlord,
            nombre='Cochera Longitud Inválida',
            direccion='Calle Sur 100',
            latitud=Decimal('19.37124500'),
            longitud=Decimal('-190.00000000'),
            metros_cuadrados=Decimal('20.00'),
            espacios_totales=1,
            tarifa_hora=Decimal('30.00'),
        )
        with self.assertRaises(ValidationError) as ctx_lng:
            invalid_lng.full_clean()
        self.assertIn('longitud', ctx_lng.exception.message_dict)

    def test_cochera_validates_surface_capacity_rate_and_non_blank_strings(self):
        invalid_surface = Cochera(
            arrendador=self.landlord,
            nombre='Cochera Sin Superficie',
            direccion='Calle Norte 10',
            latitud=Decimal('19.37124500'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('0.00'),
            espacios_totales=1,
            tarifa_hora=Decimal('40.00'),
        )
        with self.assertRaises(ValidationError) as ctx_surface:
            invalid_surface.full_clean()
        self.assertIn('metros_cuadrados', ctx_surface.exception.message_dict)

        invalid_spaces = Cochera(
            arrendador=self.landlord,
            nombre='Cochera Sin Espacios',
            direccion='Calle Norte 10',
            latitud=Decimal('19.37124500'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('15.00'),
            espacios_totales=0,
            tarifa_hora=Decimal('40.00'),
        )
        with self.assertRaises(ValidationError) as ctx_spaces:
            invalid_spaces.full_clean()
        self.assertIn('espacios_totales', ctx_spaces.exception.message_dict)

        invalid_rate = Cochera(
            arrendador=self.landlord,
            nombre='Cochera Tarifa Cero',
            direccion='Calle Norte 10',
            latitud=Decimal('19.37124500'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('15.00'),
            espacios_totales=1,
            tarifa_hora=Decimal('0.00'),
        )
        with self.assertRaises(ValidationError) as ctx_rate:
            invalid_rate.full_clean()
        self.assertIn('tarifa_hora', ctx_rate.exception.message_dict)

        blank_fields = Cochera(
            arrendador=self.landlord,
            nombre='   ',
            direccion='   ',
            latitud=Decimal('19.37124500'),
            longitud=Decimal('-99.17834200'),
            metros_cuadrados=Decimal('15.00'),
            espacios_totales=1,
            tarifa_hora=Decimal('25.00'),
        )
        with self.assertRaises(ValidationError) as ctx_blank:
            blank_fields.full_clean()
        self.assertIn('nombre', ctx_blank.exception.message_dict)
        self.assertIn('direccion', ctx_blank.exception.message_dict)


class FotoCocheraModelTests(TestCase):
    def setUp(self):
        self.landlord = _create_landlord('fotos@gmail.com')
        self.garage = Cochera.objects.create(
            arrendador=self.landlord,
            nombre='Cochera Central',
            direccion='Av. Reforma 100',
            latitud=Decimal('19.43260770'),
            longitud=Decimal('-99.13320800'),
            metros_cuadrados=Decimal('25.00'),
            espacios_totales=2,
            tarifa_hora=Decimal('50.00'),
        )

    def test_valid_image_extensions_pass(self):
        self.assertEqual(FotoCochera._meta.db_table, 'foto_cochera')
        valid_urls = [
            'https://storage.parkshare.mx/cocheras/f1.jpg',
            'https://storage.parkshare.mx/cocheras/f2.JPEG',
            'https://storage.parkshare.mx/cocheras/f3.png?size=large',
            '/media/cocheras/f4.webp',
        ]
        for url in valid_urls:
            photo = FotoCochera(cochera=self.garage, url_foto=url)
            photo.full_clean()

    def test_invalid_image_extensions_fail(self):
        for bad_url in [
            'https://storage.parkshare.mx/cocheras/doc.pdf',
            'https://storage.parkshare.mx/cocheras/anim.gif',
            'https://storage.parkshare.mx/cocheras/img.bmp',
            '   ',
        ]:
            photo = FotoCochera(cochera=self.garage, url_foto=bad_url)
            with self.assertRaises(ValidationError) as ctx:
                photo.full_clean()
            self.assertIn('url_foto', ctx.exception.message_dict)


class DisponibilidadModelTests(TestCase):
    def setUp(self):
        self.landlord = _create_landlord('horarios@gmail.com')
        self.garage = Cochera.objects.create(
            arrendador=self.landlord,
            nombre='Cochera Horario',
            direccion='Av. Universidad 300',
            latitud=Decimal('19.35000000'),
            longitud=Decimal('-99.16000000'),
            metros_cuadrados=Decimal('30.00'),
            espacios_totales=2,
            tarifa_hora=Decimal('40.00'),
        )

    def test_valid_weekdays_and_time_window_pass(self):
        self.assertEqual(Disponibilidad._meta.db_table, 'disponibilidad')
        for day in WeekdayChoice.values:
            slot = Disponibilidad(
                cochera=self.garage,
                dia_semana=day,
                hora_inicio=time(7, 0),
                hora_fin=time(22, 0),
            )
            slot.full_clean()

    def test_invalid_weekday_and_reversed_or_equal_times_fail(self):
        bad_day = Disponibilidad(
            cochera=self.garage,
            dia_semana='Z',
            hora_inicio=time(8, 0),
            hora_fin=time(18, 0),
        )
        with self.assertRaises(ValidationError) as ctx_day:
            bad_day.full_clean()
        self.assertIn('dia_semana', ctx_day.exception.message_dict)

        reversed_slot = Disponibilidad(
            cochera=self.garage,
            dia_semana=WeekdayChoice.L,
            hora_inicio=time(18, 0),
            hora_fin=time(9, 0),
        )
        with self.assertRaises(ValidationError) as ctx_rev:
            reversed_slot.full_clean()
        self.assertIn('hora_fin', ctx_rev.exception.message_dict)

        equal_slot = Disponibilidad(
            cochera=self.garage,
            dia_semana=WeekdayChoice.L,
            hora_inicio=time(10, 0),
            hora_fin=time(10, 0),
        )
        with self.assertRaises(ValidationError) as ctx_eq:
            equal_slot.full_clean()
        self.assertIn('hora_fin', ctx_eq.exception.message_dict)
