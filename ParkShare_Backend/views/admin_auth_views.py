from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ParkShare_Backend.models import (
    AccountStatus,
    DocumentoArrendador,
    Queja,
    Sancion,
    SanctionStatus,
    UserRole,
    Usuario,
    VerificationStatus,
)
from ParkShare_Backend.permissions import IsAdminRole
from ParkShare_Backend.serializers.auth_serializers import (
    AccountSuspensionSerializer,
    AdminUserListSerializer,
    LandlordRequestDetailSerializer,
    LandlordRequestListSerializer,
    ReviewLandlordRequestSerializer,
    SuspendAccountSerializer,
)
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    review_landlord_request,
    suspend_account,
)
from ParkShare_Backend.views.auth_views import (
    StandardPagination,
    handle_domain_exception,
    validation_error_response,
)


class AdminLandlordRequestListView(APIView):
    """List landlord registration requests for admin review (`GET /admin/solicitudes`)."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        status_param = (request.query_params.get('estado') or VerificationStatus.PENDIENTE).upper()
        queryset = DocumentoArrendador.objects.select_related('arrendador').order_by('-id_documento')

        if status_param != 'TODOS':
            if status_param not in VerificationStatus.values:
                return validation_error_response({'estado': ['Estado de verificación inválido.']})
            queryset = queryset.filter(estado_verificacion=status_param)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = LandlordRequestListSerializer(
            page,
            many=True,
            context={'request': request},
        )
        return paginator.get_paginated_response(serializer.data)


class AdminLandlordRequestDetailView(APIView):
    """Retrieve and resolve a landlord request (`GET/PATCH /admin/solicitudes/{id}`)."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, pk: int):
        document = (
            DocumentoArrendador.objects.select_related('arrendador')
            .filter(id_documento=pk)
            .first()
        )
        if document is None:
            return Response(
                {'detail': 'Solicitud de arrendador no encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            LandlordRequestDetailSerializer(document, context={'request': request}).data,
            status=status.HTTP_200_OK,
        )

    def patch(self, request, pk: int):
        serializer = ReviewLandlordRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        data = serializer.validated_data
        try:
            document = review_landlord_request(
                document_id=pk,
                verification_status=data['estado_verificacion'],
                rejection_reason=data.get('motivo_rechazo'),
                reason_detail=data.get('detalle_motivo'),
            )
        except (DomainValidationError, NotFoundError, ConflictError) as exc:
            return handle_domain_exception(exc)

        if document.estado_verificacion == VerificationStatus.APROBADO:
            message = 'Solicitud aprobada. La cuenta del arrendador ha sido activada.'
        else:
            message = 'Solicitud rechazada. Se ha notificado el motivo al arrendador.'

        return Response(
            {
                'mensaje': message,
                'id_documento': document.id_documento,
                'estado_verificacion': document.estado_verificacion,
                'motivo_rechazo': document.motivo_rechazo,
                'arrendador_estado_cuenta': document.arrendador.estado_cuenta,
            },
            status=status.HTTP_200_OK,
        )


class AdminAccountListView(APIView):
    """Search and list user accounts (`GET /admin/cuentas?q=`)."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        queryset = Usuario.objects.annotate(
            _quejas_recibidas_count=Count('quejas_recibidas', distinct=True),
            _quejas_enviadas_count=Count('quejas_enviadas', distinct=True),
            _sanciones_count=Count('sanciones', distinct=True),
            _suspensiones_count=Count('received_suspensions', distinct=True),
        ).order_by('id_usuario')

        query = (request.query_params.get('q') or '').strip()
        if query:
            queryset = queryset.filter(
                Q(nombre_completo__icontains=query) | Q(correo__icontains=query)
            )

        role_param = (request.query_params.get('rol') or '').strip().upper()
        if role_param:
            if role_param not in UserRole.values:
                return validation_error_response({'rol': ['Rol de usuario inválido.']})
            queryset = queryset.filter(rol=role_param)

        account_status = (request.query_params.get('estado_cuenta') or '').strip().upper()
        if account_status:
            if account_status not in AccountStatus.values:
                return validation_error_response({'estado_cuenta': ['Estado de cuenta inválido.']})
            queryset = queryset.filter(estado_cuenta=account_status)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = AdminUserListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminAccountDetailView(APIView):
    """Retrieve user profile and disciplinary history (`GET /admin/cuentas/{id}`)."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, pk: int):
        user = (
            Usuario.objects.annotate(
                _quejas_recibidas_count=Count('quejas_recibidas', distinct=True),
                _quejas_enviadas_count=Count('quejas_enviadas', distinct=True),
                _sanciones_count=Count('sanciones', distinct=True),
            )
            .prefetch_related('received_suspensions')
            .filter(id_usuario=pk)
            .first()
        )
        if user is None:
            return Response(
                {'detail': 'Usuario no encontrado.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        def _iso_local(dt_val):
            if dt_val is None:
                return ''
            if hasattr(dt_val, 'isoformat'):
                if timezone.is_aware(dt_val):
                    return timezone.localtime(dt_val).isoformat()
                return dt_val.isoformat()
            return str(dt_val)

        expected_complaints = int(
            (
                user._quejas_recibidas_count
                if user.rol == UserRole.ARRENDADOR
                else user._quejas_enviadas_count
            )
            or 0
        )
        expected_sanctions = int(user._sanciones_count or 0)

        if expected_complaints > 0:
            if user.rol == UserRole.ARRENDADOR:
                complaints = list(
                    Queja.objects.filter(arrendador=user).order_by('-fecha_creacion', '-id_queja')
                )
            else:
                complaints = list(
                    Queja.objects.filter(arrendatario=user).order_by('-fecha_creacion', '-id_queja')
                )
        else:
            complaints = []

        if expected_sanctions > 0:
            sanctions = list(
                Sancion.objects.filter(arrendador=user).order_by('-fecha_creacion', '-id_sancion')
            )
        else:
            sanctions = []

        suspensions = sorted(
            list(user.received_suspensions.all()),
            key=lambda s: (s.fecha_suspension, s.id_suspension),
            reverse=True,
        )

        return Response(
            {
                'id_usuario': user.id_usuario,
                'nombre_completo': user.nombre_completo,
                'correo': user.correo,
                'telefono': user.telefono,
                'fecha_nacimiento': user.fecha_nacimiento,
                'rol': user.rol,
                'estado_cuenta': user.estado_cuenta,
                'placas': user.placas,
                'modelo_vehiculo': user.modelo_vehiculo,
                'cuenta_cobro': user.cuenta_cobro,
                'resumen_disciplinario': {
                    'total_quejas': len(complaints),
                    'total_sanciones': len(sanctions),
                    'sanciones_vigentes': sum(
                        1 for item in sanctions if item.estado == SanctionStatus.VIGENTE
                    ),
                    'total_suspensiones': len(suspensions),
                },
                'historial_quejas': [
                    {
                        'id_queja': item.id_queja,
                        'reserva_id': item.reserva_id,
                        'motivo_categoria': item.motivo_categoria,
                        'descripcion': item.descripcion,
                        'estado': item.estado,
                        'fecha_creacion': _iso_local(item.fecha_creacion),
                    }
                    for item in complaints
                ],
                'historial_sanciones': [
                    {
                        'id_sancion': item.id_sancion,
                        'queja_id': item.queja_id,
                        'motivo': item.motivo,
                        'estado': item.estado,
                        'fecha_creacion': _iso_local(item.fecha_creacion),
                    }
                    for item in sanctions
                ],
                'historial_suspensiones': AccountSuspensionSerializer(suspensions, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class AdminAccountSuspendView(APIView):
    """Suspend a user account (`POST /admin/cuentas/{id}/suspender`)."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk: int):
        serializer = SuspendAccountSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            suspension = suspend_account(
                target_user_id=pk,
                admin_user=request.user,
                reason=serializer.validated_data.get('motivo', ''),
            )
        except (DomainValidationError, NotFoundError, ConflictError) as exc:
            return handle_domain_exception(exc)

        return Response(
            {
                'mensaje': 'Cuenta suspendida exitosamente.',
                'suspension': AccountSuspensionSerializer(suspension).data,
                'estado_cuenta': AccountStatus.SUSPENDIDA,
            },
            status=status.HTTP_200_OK,
        )
