from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ParkShare_Backend.permissions import IsActiveAccount, IsActiveLandlord, IsAdminRole
from ParkShare_Backend.serializers.cochera_serializers import (
    AdminGarageReviewListSerializer,
    CocheraAdminValidationSerializer,
    CocheraCreateSerializer,
    CocheraDetailSerializer,
    CocheraResponseSerializer,
    CocheraSearchResultSerializer,
    CocheraUpdateSerializer,
    LandlordGarageListSerializer,
)
from ParkShare_Backend.services.auth_service import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from ParkShare_Backend.services.cochera_service import (
    create_garage,
    get_garage_detail_for_user,
    list_admin_garages,
    list_landlord_garages,
    search_nearby_garages,
    update_garage,
    validate_garage_publication,
)
from ParkShare_Backend.views.auth_views import (
    StandardPagination,
    handle_domain_exception,
    validation_error_response,
)


class GarageListCreateView(APIView):
    """Search nearby approved garages (`GET /cocheras`) or register a new garage (`POST /cocheras`)."""

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated(), IsActiveLandlord()]
        return [IsAuthenticated(), IsActiveAccount()]

    def get(self, request):
        try:
            garages = search_nearby_garages(
                lat=request.query_params.get('lat'),
                lng=request.query_params.get('lng'),
                order_by=request.query_params.get('orden', 'cercania'),
                radius_km=request.query_params.get('radio_km'),
                vehicle_type=request.query_params.get('tipo_vehiculo'),
                search_query=(
                    request.query_params.get('q')
                    or request.query_params.get('busqueda')
                ),
            )
        except DomainValidationError as exc:
            return handle_domain_exception(exc)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(garages, request, view=self)
        serializer = CocheraSearchResultSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = CocheraCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            garage = create_garage(request.user, serializer.validated_data)
        except (DomainValidationError, PermissionDeniedError, ConflictError) as exc:
            return handle_domain_exception(exc)

        return Response(CocheraResponseSerializer(garage).data, status=status.HTTP_201_CREATED)


class LandlordGarageListView(APIView):
    """List all garages owned by the authenticated active landlord (`GET /cocheras/mis-cocheras`)."""

    permission_classes = [IsAuthenticated, IsActiveLandlord]

    def get(self, request):
        try:
            garages = list_landlord_garages(
                request.user,
                status_filter=request.query_params.get('estado'),
            )
        except (DomainValidationError, PermissionDeniedError) as exc:
            return handle_domain_exception(exc)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(garages, request, view=self)
        serializer = LandlordGarageListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class GarageDetailUpdateView(APIView):
    """Retrieve garage detail (`GET /cocheras/{id}`) or update own garage (`PATCH /cocheras/{id}`)."""

    def get_permissions(self):
        if self.request.method == 'PATCH':
            return [IsAuthenticated(), IsActiveLandlord()]
        return [IsAuthenticated(), IsActiveAccount()]

    def get(self, request, pk: int):
        try:
            garage = get_garage_detail_for_user(pk, request.user)
        except (NotFoundError, PermissionDeniedError) as exc:
            return handle_domain_exception(exc)

        return Response(CocheraDetailSerializer(garage).data, status=status.HTTP_200_OK)

    def patch(self, request, pk: int):
        serializer = CocheraUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        try:
            garage = update_garage(pk, request.user, serializer.validated_data)
        except (DomainValidationError, PermissionDeniedError, NotFoundError, ConflictError) as exc:
            return handle_domain_exception(exc)

        return Response(CocheraDetailSerializer(garage).data, status=status.HTTP_200_OK)


class AdminGarageListView(APIView):
    """List garages pending administrative review (`GET /admin/cocheras`)."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        try:
            garages = list_admin_garages(
                request.user,
                status_filter=request.query_params.get('estado'),
            )
        except (DomainValidationError, PermissionDeniedError) as exc:
            return handle_domain_exception(exc)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(garages, request, view=self)
        serializer = AdminGarageReviewListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminGarageValidationView(APIView):
    """Approve or reject a pending garage publication (`PATCH /admin/cocheras/{id}/validar`)."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def patch(self, request, pk: int):
        serializer = CocheraAdminValidationSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        data = serializer.validated_data
        try:
            result = validate_garage_publication(
                garage_id=pk,
                admin_user=request.user,
                new_status=data['estado_cochera'],
                reason=data.get('motivo'),
            )
        except (DomainValidationError, PermissionDeniedError, NotFoundError, ConflictError) as exc:
            return handle_domain_exception(exc)

        return Response(result, status=status.HTTP_200_OK)
