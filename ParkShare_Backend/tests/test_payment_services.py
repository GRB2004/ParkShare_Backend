from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase

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
    Usuario,
    VehicleType,
    WeekdayChoice,
    WithdrawalStatus,
)
from ParkShare_Backend.puentes.payment_gateway import SimulatedPaymentGatewayAdapter
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from ParkShare_Backend.services.payment_service import (
    apply_late_checkout_charge,
    get_host_financial_summary,
    handle_reservation_cancellation,
    mask_payout_account,
    process_reservation_payment,
    withdraw_host_balance,
)
from ParkShare_Backend.services.qr_service import get_active_qr_for_reservation, validate_scanned_qr
from ParkShare_Backend.services.reserva_service import (
    create_reservation,
    finish_reservation,
    start_reservation_usage,
)

MEXICO_TZ = ZoneInfo('America/Mexico_City')


def _create_user(
    email: str,
    role: str,
    status: str = AccountStatus.ACTIVA,
    payout_account: str | None = '012180001234567890',
) -> Usuario:
    extra = {'cuenta_cobro': payout_account} if role == UserRole.ARRENDADOR else {
        'placas': 'ABC-123-D',
        'modelo_vehiculo': 'Nissan Versa 2022',
    }
    return Usuario.objects.create_user(
        nombre_completo='Usuario Servicios Pagos',
        correo=email,
        telefono='5512345678',
        password='MiPasswordSegura123!',
        fecha_nacimiento=date(1990, 5, 10),
        rol=role,
        estado_cuenta=status,
        **extra,
    )


def _create_garage(landlord: Usuario, rate: str = '40.00') -> Cochera:
    garage = Cochera.objects.create(
        arrendador=landlord,
        nombre='Cochera Centro Norte',
        direccion='Av. Insurgentes Centro 100',
        latitud=Decimal('19.43000000'),
        longitud=Decimal('-99.15000000'),
        metros_cuadrados=Decimal('35.00'),
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


class PaymentServicesTests(TestCase):
    def setUp(self):
        self.landlord = _create_user('host.fin@gmail.com', UserRole.ARRENDADOR, payout_account='0121800012344321')
        self.pending_landlord = _create_user(
            'pending.fin@gmail.com', UserRole.ARRENDADOR, status=AccountStatus.PENDIENTE
        )
        self.tenant = _create_user('tenant.fin@outlook.com', UserRole.ARRENDATARIO)
        self.other_tenant = _create_user('tenant2.fin@outlook.com', UserRole.ARRENDATARIO)
        self.garage = _create_garage(self.landlord, rate='40.00')

        self.now = datetime(2026, 11, 25, 14, 0, tzinfo=MEXICO_TZ)
        self.start_dt = datetime(2026, 11, 25, 16, 0, tzinfo=MEXICO_TZ)
        self.end_dt = datetime(2026, 11, 25, 18, 0, tzinfo=MEXICO_TZ)

    def test_simulated_gateway_adapter_approve_decline_refund_payout(self):
        adapter = SimulatedPaymentGatewayAdapter()
        ok_res = adapter.charge(Decimal('80.00'), 'TARJETA', token_pago='tok_visa_4242')
        self.assertTrue(ok_res.approved)
        self.assertTrue(ok_res.reference.startswith('PS-PAY-'))

        decl_res = adapter.charge(Decimal('80.00'), 'TARJETA', token_pago='tok_rechazado')
        self.assertFalse(decl_res.approved)
        self.assertTrue(decl_res.reference.startswith('PS-DECL-'))

        ref_res = adapter.refund(ok_res.reference, Decimal('80.00'))
        self.assertTrue(ref_res.approved)
        self.assertTrue(ref_res.reference.startswith('PS-REF-'))

        pay_res = adapter.payout('0121800012344321', Decimal('420.00'))
        self.assertTrue(pay_res.approved)
        self.assertTrue(pay_res.reference.startswith('PS-RET-'))

    def test_process_reservation_payment_approved_confirms_and_issues_entry_qr(self):
        reservation = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=self.now
        )
        payment, payload = process_reservation_payment(
            reservation.id_reserva,
            self.tenant,
            metodo='TARJETA',
            token_pago='tok_visa_4242',
            now=self.now,
        )
        self.assertEqual(payment.estado, PaymentStatus.APROBADO)
        self.assertEqual(payment.monto_total, Decimal('80.00'))
        self.assertEqual(payload['estado'], PaymentStatus.APROBADO)
        self.assertEqual(payload['estado_reserva'], ReservationStatus.CONFIRMADA)
        self.assertTrue(payload['qr_entrada_generado'])

        reservation.refresh_from_db()
        self.assertEqual(reservation.estado, ReservationStatus.CONFIRMADA)
        self.assertTrue(
            CodigoQR.objects.filter(reserva=reservation, tipo_qr=QRCodeType.ENTRADA, usado=False).exists()
        )

        # Duplicate payment attempt on already confirmed/paid reservation -> ConflictError
        with self.assertRaises(ConflictError):
            process_reservation_payment(
                reservation.id_reserva,
                self.tenant,
                metodo='TARJETA',
                token_pago='tok_visa_4242',
                now=self.now,
            )

    def test_process_reservation_payment_rejected_cancels_without_qr(self):
        reservation = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=self.now
        )
        payment, payload = process_reservation_payment(
            reservation.id_reserva,
            self.tenant,
            metodo='TARJETA',
            token_pago='tok_rechazado',
            now=self.now,
        )
        self.assertEqual(payment.estado, PaymentStatus.RECHAZADO)
        self.assertEqual(payload['estado'], PaymentStatus.RECHAZADO)
        self.assertEqual(payload['estado_reserva'], ReservationStatus.CANCELADA)
        self.assertFalse(payload['qr_entrada_generado'])

        reservation.refresh_from_db()
        self.assertEqual(reservation.estado, ReservationStatus.CANCELADA)
        self.assertFalse(CodigoQR.objects.filter(reserva=reservation).exists())

        # Retry on cancelled reservation -> ConflictError
        with self.assertRaises(ConflictError):
            process_reservation_payment(
                reservation.id_reserva,
                self.tenant,
                metodo='TARJETA',
                token_pago='tok_visa_4242',
                now=self.now,
            )

    def test_apply_late_checkout_charge_rn02_and_rn03_via_qr_checkout(self):
        reservation = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=self.now
        )
        payment, _ = process_reservation_payment(
            reservation.id_reserva, self.tenant, metodo='TARJETA', now=self.now
        )
        entry_qr = get_active_qr_for_reservation(reservation.id_reserva, self.tenant)
        validate_scanned_qr(entry_qr.token_qr, self.landlord, now=self.start_dt)

        exit_qr = get_active_qr_for_reservation(reservation.id_reserva, self.tenant)
        # 20 minutes after end_dt -> 10 min tolerance (RN-02) + 10 min late = $100.00 (RN-03)
        validate_scanned_qr(exit_qr.token_qr, self.landlord, now=self.end_dt + timedelta(minutes=20))

        payment.refresh_from_db()
        self.assertEqual(payment.monto_total, Decimal('180.00'))

    def test_handle_reservation_cancellation_rn04_refund_and_rn05_penalty(self):
        # RN-04: Cancel within 10 minutes -> penalty 0.00, payment REEMBOLSADO
        r1 = create_reservation(
            self.tenant, self.garage.id_cochera, self.start_dt, self.end_dt, now=self.now
        )
        p1, _ = process_reservation_payment(
            r1.id_reserva, self.tenant, metodo='TARJETA', now=self.now
        )
        handle_reservation_cancellation(r1, now=r1.fecha_creacion + timedelta(minutes=5))
        r1.refresh_from_db()
        p1.refresh_from_db()
        self.assertEqual(r1.estado, ReservationStatus.CANCELADA)
        self.assertEqual(r1.monto_penalizacion, Decimal('0.00'))
        self.assertEqual(p1.estado, PaymentStatus.REEMBOLSADO)

        # RN-05: Cancel after 10 minutes -> penalty 100.00 reflected in financial record
        r2 = create_reservation(
            self.other_tenant,
            self.garage.id_cochera,
            self.start_dt + timedelta(hours=3),
            self.end_dt + timedelta(hours=3),
            now=self.now,
        )
        p2, _ = process_reservation_payment(
            r2.id_reserva, self.other_tenant, metodo='TARJETA', now=self.now
        )
        handle_reservation_cancellation(r2, now=r2.fecha_creacion + timedelta(minutes=15))
        r2.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(r2.estado, ReservationStatus.CANCELADA)
        self.assertEqual(r2.monto_penalizacion, Decimal('100.00'))
        self.assertEqual(p2.monto_total, Decimal('100.00'))

    def test_host_financial_summary_timezone_and_withdraw_host_balance(self):
        self.assertEqual(mask_payout_account('0121800012344321'), '****4321')

        # 1. Finalized today at 23:30 Mexico City time (05:30 next day UTC) -> $180.00
        today_late_start = datetime(2026, 11, 25, 21, 0, tzinfo=MEXICO_TZ)
        today_late_end = datetime(2026, 11, 25, 23, 0, tzinfo=MEXICO_TZ)
        today_checkout = datetime(2026, 11, 25, 23, 30, tzinfo=MEXICO_TZ)
        r_today = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=today_late_start,
            fecha_hora_fin=today_late_end,
            hora_salida_real=today_checkout,
            monto_base=Decimal('80.00'),
            monto_penalizacion=Decimal('100.00'),
            estado=ReservationStatus.FINALIZADA,
        )
        Pago.objects.create(
            reserva=r_today,
            monto_total=Decimal('180.00'),
            metodo='TARJETA',
            referencia_pasarela='PS-PAY-TODAY',
            estado=PaymentStatus.APROBADO,
            fecha_pago=today_late_start,
        )

        # 2. Finalized yesterday -> $240.00
        yest_start = datetime(2026, 11, 24, 10, 0, tzinfo=MEXICO_TZ)
        yest_end = datetime(2026, 11, 24, 16, 0, tzinfo=MEXICO_TZ)
        r_yest = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=yest_start,
            fecha_hora_fin=yest_end,
            hora_salida_real=yest_end,
            monto_base=Decimal('240.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.FINALIZADA,
        )
        Pago.objects.create(
            reserva=r_yest,
            monto_total=Decimal('240.00'),
            metodo='TARJETA',
            referencia_pasarela='PS-PAY-YEST',
            estado=PaymentStatus.APROBADO,
            fecha_pago=yest_start,
        )

        # 3. Confirmed upcoming (Pendiente de uso) -> $70.00
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
            referencia_pasarela='PS-PAY-UP',
            estado=PaymentStatus.APROBADO,
            fecha_pago=self.now,
        )

        # 4. Refunded (RN-04) -> $50.00 (should not count)
        r_ref = Reserva.objects.create(
            arrendatario=self.tenant,
            cochera=self.garage,
            fecha_hora_inicio=datetime(2026, 11, 24, 18, 0, tzinfo=MEXICO_TZ),
            fecha_hora_fin=datetime(2026, 11, 24, 19, 0, tzinfo=MEXICO_TZ),
            monto_base=Decimal('50.00'),
            monto_penalizacion=Decimal('0.00'),
            estado=ReservationStatus.CANCELADA,
        )
        Pago.objects.create(
            reserva=r_ref,
            monto_total=Decimal('50.00'),
            metodo='TARJETA',
            referencia_pasarela='PS-PAY-REF',
            estado=PaymentStatus.REEMBOLSADO,
            fecha_pago=yest_start,
        )

        summary = get_host_financial_summary(
            self.landlord,
            now=datetime(2026, 11, 25, 23, 45, tzinfo=MEXICO_TZ),
        )
        self.assertEqual(summary['ganado_hoy'], '180.00')
        self.assertEqual(summary['disponible_para_retirar'], '420.00')
        self.assertEqual(summary['cuenta_cobro'], '****4321')
        self.assertEqual(len(summary['movimientos']), 3)

        # Withdraw full available balance
        withdrawal_res = withdraw_host_balance(
            self.landlord,
            now=datetime(2026, 11, 25, 23, 50, tzinfo=MEXICO_TZ),
        )
        self.assertEqual(withdrawal_res['monto_retirado'], '420.00')
        self.assertEqual(withdrawal_res['disponible_para_retirar'], '0.00')
        self.assertEqual(withdrawal_res['cuenta_cobro'], '****4321')
        self.assertEqual(withdrawal_res['estado'], WithdrawalStatus.COMPLETADO)

        # Summary after withdrawal preserves ganado_hoy but disponible_para_retirar is 0.00
        summary_after = get_host_financial_summary(
            self.landlord,
            now=datetime(2026, 11, 25, 23, 55, tzinfo=MEXICO_TZ),
        )
        self.assertEqual(summary_after['ganado_hoy'], '180.00')
        self.assertEqual(summary_after['disponible_para_retirar'], '0.00')

        # Second withdrawal with 0.00 available -> ConflictError (NoFundsAvailable)
        with self.assertRaises(ConflictError) as cm:
            withdraw_host_balance(self.landlord)
        self.assertEqual(cm.exception.error_code, 'NoFundsAvailable')
