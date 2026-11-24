from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
import secrets

from django.conf import settings
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class GatewayResult:
    """Normalized result returned by a payment gateway bridge operation."""

    approved: bool
    reference: str | None
    message: str


class PaymentGatewayBridge(ABC):
    """Abstract Bridge interface for external or simulated payment gateways (CU-07)."""

    @abstractmethod
    def charge(
        self,
        amount: Decimal,
        method: str,
        token_pago: str | None = None,
        metadata: dict | None = None,
    ) -> GatewayResult:
        """Attempt to charge the given amount using the specified payment method and token."""

    @abstractmethod
    def refund(
        self,
        reference: str | None,
        amount: Decimal,
    ) -> GatewayResult:
        """Process a full or partial refund for a previous transaction reference."""

    @abstractmethod
    def payout(
        self,
        destination_account: str,
        amount: Decimal,
        metadata: dict | None = None,
    ) -> GatewayResult:
        """Transfer funds to the landlord's destination payout account."""


class SimulatedPaymentGatewayAdapter(PaymentGatewayBridge):
    """Deterministic simulated payment gateway adapter for development and testing."""

    DECLINED_TOKENS = ('tok_rechazado', 'tok_declined', 'declined', 'rechazado')

    def charge(
        self,
        amount: Decimal,
        method: str,
        token_pago: str | None = None,
        metadata: dict | None = None,
    ) -> GatewayResult:
        normalized_token = (token_pago or '').strip().lower()
        if normalized_token in self.DECLINED_TOKENS or normalized_token.startswith('tok_rechazado'):
            return GatewayResult(
                approved=False,
                reference=f'PS-DECL-{secrets.token_hex(4)}',
                message='El pago fue rechazado por la pasarela. La reserva ha sido cancelada.',
            )
        return GatewayResult(
            approved=True,
            reference=f'PS-PAY-{secrets.token_hex(6)}',
            message='Pago aprobado. Tu reserva está confirmada y tu código QR de entrada está listo.',
        )

    def refund(
        self,
        reference: str | None,
        amount: Decimal,
    ) -> GatewayResult:
        return GatewayResult(
            approved=True,
            reference=f'PS-REF-{secrets.token_hex(6)}',
            message='Reembolso procesado exitosamente.',
        )

    def payout(
        self,
        destination_account: str,
        amount: Decimal,
        metadata: dict | None = None,
    ) -> GatewayResult:
        return GatewayResult(
            approved=True,
            reference=f'PS-RET-{secrets.token_hex(5)}',
            message='Retiro procesado correctamente hacia tu cuenta de cobro.',
        )


def get_payment_gateway() -> PaymentGatewayBridge:
    """Resolve the active PaymentGatewayBridge adapter from Django settings or default simulator."""
    adapter_path = getattr(settings, 'PAYMENT_GATEWAY_ADAPTER', None)
    if isinstance(adapter_path, str) and adapter_path.strip():
        adapter_cls = import_string(adapter_path.strip())
        return adapter_cls()
    return SimulatedPaymentGatewayAdapter()
