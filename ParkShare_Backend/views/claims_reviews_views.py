from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ParkShare_Backend.permissions import IsActiveAccount, IsAdminRole
from ParkShare_Backend.serializers.claims_reviews_serializers import (
    CreateComplaintInputSerializer,
    CreateReviewInputSerializer,
    QuejaResponseSerializer,
    ResenaResponseSerializer,
    ResolveAppealInputSerializer,
    ResolveComplaintInputSerializer,
    SubmitAppealInputSerializer,
)
from ParkShare_Backend.services.auth_service import (
    AccountSuspendedError,
    AuthenticationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
    delete_saved_attachment_by_url,
    save_uploaded_attachment,
)
from ParkShare_Backend.services.claim_sanction_service import (
    create_complaint,
    get_admin_appeal_detail,
    get_admin_complaint_detail,
    get_landlord_sanction_detail,
    list_admin_claims_and_appeals,
    list_landlord_sanctions,
    resolve_appeal,
    resolve_complaint,
    submit_appeal,
)
from ParkShare_Backend.services.review_service import create_review
from ParkShare_Backend.views.auth_views import (
    handle_domain_exception,
    validation_error_response,
)


def _format_iso_fields(data: dict, keys: tuple[str, ...]) -> dict:
    for key in keys:
        val = data.get(key)
        if hasattr(val, 'isoformat'):
            if timezone.is_aware(val):
                val = timezone.localtime(val)
            data[key] = val.isoformat()
    return data


class ReservationReviewCreateView(APIView):
    """POST /reservas/{id}/resenas — Create a bilateral post-stay review (CU-09)."""

    permission_classes = [IsAuthenticated, IsActiveAccount]

    def post(self, request, pk: int):
        serializer = CreateReviewInputSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            review = create_review(
                reservation_id=pk,
                author=request.user,
                calificacion=serializer.validated_data['calificacion'],
                etiquetas=serializer.validated_data.get('etiquetas'),
                comentario=serializer.validated_data.get('comentario'),
            )
            return Response(ResenaResponseSerializer(review).data, status=status.HTTP_201_CREATED)
        except (
            DomainValidationError,
            AuthenticationError,
            AccountSuspendedError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
        ) as exc:
            return handle_domain_exception(exc)


class ComplaintCreateView(APIView):
    """POST /quejas — Submit a tenant complaint about a reservation (CU-12)."""

    permission_classes = [IsAuthenticated, IsActiveAccount]

    def post(self, request):
        serializer = CreateComplaintInputSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            complaint = create_complaint(
                reservation_id=serializer.validated_data['reserva_id'],
                renter=request.user,
                motivo_categoria=serializer.validated_data['motivo_categoria'],
                descripcion=serializer.validated_data['descripcion'],
            )
            return Response(QuejaResponseSerializer(complaint).data, status=status.HTTP_201_CREATED)
        except (
            DomainValidationError,
            AuthenticationError,
            AccountSuspendedError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
        ) as exc:
            return handle_domain_exception(exc)


def _resolve_evidence_url(request, raw_url: str | None) -> str | None:
    cleaned = (raw_url or '').strip()
    if not cleaned:
        return None
    if request is not None and cleaned.startswith('/'):
        return request.build_absolute_uri(cleaned)
    return cleaned


class AdminClaimAppealListView(APIView):
    """GET /admin/quejas — List pending complaints and appeals under review (CU-13)."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        try:
            result = list_admin_claims_and_appeals(
                admin_user=request.user,
                estado_queja=request.query_params.get('estado_queja'),
                estado_apelacion=request.query_params.get('estado_apelacion'),
            )
            for item in result.get('quejas_pendientes', []):
                _format_iso_fields(item, ('fecha_creacion',))
            for item in result.get('apelaciones_en_revision', []):
                _format_iso_fields(item, ('fecha_creacion',))
                item['archivo_evidencia_url'] = _resolve_evidence_url(
                    request, item.get('url_evidencia')
                )
            return Response(result, status=status.HTTP_200_OK)
        except (DomainValidationError, PermissionDeniedError) as exc:
            return handle_domain_exception(exc)


class AdminComplaintDetailResolveView(APIView):
    """GET/PATCH /admin/quejas/{id} — Inspect and resolve a tenant complaint (CU-13)."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, pk: int):
        try:
            result = get_admin_complaint_detail(complaint_id=pk, admin_user=request.user)
            _format_iso_fields(result, ('fecha_creacion',))
            if isinstance(result.get('reserva'), dict):
                _format_iso_fields(result['reserva'], ('fecha_hora_inicio', 'fecha_hora_fin'))
            if isinstance(result.get('sancion'), dict):
                _format_iso_fields(result['sancion'], ('fecha_creacion',))
            return Response(result, status=status.HTTP_200_OK)
        except (PermissionDeniedError, NotFoundError) as exc:
            return handle_domain_exception(exc)

    def patch(self, request, pk: int):
        serializer = ResolveComplaintInputSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            result = resolve_complaint(
                complaint_id=pk,
                admin_user=request.user,
                new_status=serializer.validated_data['estado'],
                motivo_sancion=serializer.validated_data.get('motivo_sancion'),
            )
            if isinstance(result.get('sancion'), dict):
                _format_iso_fields(result['sancion'], ('fecha_creacion',))
            return Response(result, status=status.HTTP_200_OK)
        except (
            DomainValidationError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
        ) as exc:
            return handle_domain_exception(exc)


class LandlordSanctionListView(APIView):
    """GET /arrendador/sanciones — List all sanctions for the authenticated landlord (CU-13)."""

    permission_classes = [IsAuthenticated, IsActiveAccount]

    def get(self, request):
        try:
            result = list_landlord_sanctions(
                host_user=request.user,
                estado_filter=request.query_params.get('estado'),
            )
            for item in result.get('sanciones', []):
                _format_iso_fields(item, ('fecha_creacion',))
            return Response(result, status=status.HTTP_200_OK)
        except (DomainValidationError, PermissionDeniedError) as exc:
            return handle_domain_exception(exc)


class LandlordSanctionDetailView(APIView):
    """GET /arrendador/sanciones/{id} — Retrieve detail of a landlord's sanction (CU-13)."""

    permission_classes = [IsAuthenticated, IsActiveAccount]

    def get(self, request, pk: int):
        try:
            result = get_landlord_sanction_detail(sanction_id=pk, host_user=request.user)
            _format_iso_fields(result, ('fecha_creacion',))
            if isinstance(result.get('queja_resumen'), dict):
                _format_iso_fields(result['queja_resumen'], ('fecha_creacion',))
            if isinstance(result.get('apelacion'), dict):
                _format_iso_fields(result['apelacion'], ('fecha_creacion',))
            return Response(result, status=status.HTTP_200_OK)
        except (PermissionDeniedError, NotFoundError) as exc:
            return handle_domain_exception(exc)


class SanctionAppealCreateView(APIView):
    """POST /sanciones/{id}/apelar — Submit an appeal for a VIGENTE sanction (CU-13)."""

    permission_classes = [IsAuthenticated, IsActiveAccount]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request, pk: int):
        file_aliases = ('archivo_evidencia', 'evidencia', 'archivo', 'url_evidencia')
        excluded_keys = {'archivo_evidencia', 'evidencia', 'archivo'}
        payload = {
            key: val
            for key, val in request.data.items()
            if key not in excluded_keys and not hasattr(val, 'read')
        }

        saved_evidence_url: str | None = None
        try:
            uploaded_file = None
            for alias in file_aliases:
                if alias in request.FILES:
                    uploaded_file = request.FILES[alias]
                    break
            if uploaded_file is not None:
                saved_info = save_uploaded_attachment(uploaded_file, folder='evidencias')
                saved_evidence_url = saved_info['url']
                payload['url_evidencia'] = saved_evidence_url

            serializer = SubmitAppealInputSerializer(data=payload)
            if not serializer.is_valid():
                if saved_evidence_url:
                    delete_saved_attachment_by_url(saved_evidence_url)
                return validation_error_response(serializer.errors)

            result = submit_appeal(
                sanction_id=pk,
                host_user=request.user,
                descripcion=serializer.validated_data['descripcion'],
                url_evidencia=serializer.validated_data.get('url_evidencia'),
            )
            _format_iso_fields(result, ('fecha_creacion',))
            result['archivo_evidencia_url'] = _resolve_evidence_url(
                request, result.get('url_evidencia')
            )
            return Response(result, status=status.HTTP_201_CREATED)
        except (
            DomainValidationError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
        ) as exc:
            if saved_evidence_url:
                delete_saved_attachment_by_url(saved_evidence_url)
            return handle_domain_exception(exc)
        except Exception:
            if saved_evidence_url:
                delete_saved_attachment_by_url(saved_evidence_url)
            raise


class AdminAppealResolveView(APIView):
    """GET/PATCH /admin/apelaciones/{id} — Inspect and resolve a landlord's sanction appeal (CU-13)."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, pk: int):
        try:
            result = get_admin_appeal_detail(appeal_id=pk, admin_user=request.user)
            _format_iso_fields(result, ('fecha_creacion',))
            if isinstance(result.get('sancion'), dict):
                _format_iso_fields(result['sancion'], ('fecha_creacion',))
            result['archivo_evidencia_url'] = _resolve_evidence_url(
                request, result.get('url_evidencia')
            )
            return Response(result, status=status.HTTP_200_OK)
        except (PermissionDeniedError, NotFoundError) as exc:
            return handle_domain_exception(exc)

    def patch(self, request, pk: int):
        serializer = ResolveAppealInputSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            result = resolve_appeal(
                appeal_id=pk,
                admin_user=request.user,
                new_status=serializer.validated_data['estado'],
            )
            return Response(result, status=status.HTTP_200_OK)
        except (
            DomainValidationError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
        ) as exc:
            return handle_domain_exception(exc)
