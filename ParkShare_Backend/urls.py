"""
URL configuration for ParkShare_Backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
"""
from django.contrib import admin
from django.urls import include, path, re_path

from .views import (
    AdminAccountDetailView,
    AdminAccountListView,
    AdminAccountSuspendView,
    AdminGarageListView,
    AdminGarageValidationView,
    AdminLandlordRequestDetailView,
    AdminLandlordRequestListView,
    GarageDetailUpdateView,
    GarageListCreateView,
    HostEarningsView,
    HostWithdrawView,
    LandlordGarageListView,
    LoginView,
    ProcessPaymentView,
    RegisterView,
    ReservationCancelView,
    ReservationDetailUpdateView,
    ReservationListCreateView,
    ReservationQRDetailView,
    SimulateQRView,
    ValidateQRView,
    home_view,
)


def get_api_urlpatterns(name_prefix: str = ''):
    return [
        re_path(r'^auth/registro/?$', RegisterView.as_view(), name=f'{name_prefix}auth-register'),
        re_path(r'^auth/login/?$', LoginView.as_view(), name=f'{name_prefix}auth-login'),
        re_path(
            r'^admin/solicitudes/?$',
            AdminLandlordRequestListView.as_view(),
            name=f'{name_prefix}admin-solicitudes-list',
        ),
        re_path(
            r'^admin/solicitudes/(?P<pk>\d+)/?$',
            AdminLandlordRequestDetailView.as_view(),
            name=f'{name_prefix}admin-solicitudes-detail',
        ),
        re_path(
            r'^admin/cuentas/?$',
            AdminAccountListView.as_view(),
            name=f'{name_prefix}admin-cuentas-list',
        ),
        re_path(
            r'^admin/cuentas/(?P<pk>\d+)/?$',
            AdminAccountDetailView.as_view(),
            name=f'{name_prefix}admin-cuentas-detail',
        ),
        re_path(
            r'^admin/cuentas/(?P<pk>\d+)/suspender/?$',
            AdminAccountSuspendView.as_view(),
            name=f'{name_prefix}admin-cuentas-suspender',
        ),
        re_path(
            r'^cocheras/?$',
            GarageListCreateView.as_view(),
            name=f'{name_prefix}cocheras-list-create',
        ),
        re_path(
            r'^cocheras/mis-cocheras/?$',
            LandlordGarageListView.as_view(),
            name=f'{name_prefix}cocheras-mis-cocheras',
        ),
        re_path(
            r'^cocheras/(?P<pk>\d+)/?$',
            GarageDetailUpdateView.as_view(),
            name=f'{name_prefix}cocheras-detail',
        ),
        re_path(
            r'^admin/cocheras/?$',
            AdminGarageListView.as_view(),
            name=f'{name_prefix}admin-cocheras-list',
        ),
        re_path(
            r'^admin/cocheras/(?P<pk>\d+)/validar/?$',
            AdminGarageValidationView.as_view(),
            name=f'{name_prefix}admin-cocheras-validar',
        ),
        re_path(
            r'^reservas/?$',
            ReservationListCreateView.as_view(),
            name=f'{name_prefix}reservas-list-create',
        ),
        re_path(
            r'^reservas/(?P<pk>\d+)/?$',
            ReservationDetailUpdateView.as_view(),
            name=f'{name_prefix}reservas-detail',
        ),
        re_path(
            r'^reservas/(?P<pk>\d+)/cancelar/?$',
            ReservationCancelView.as_view(),
            name=f'{name_prefix}reservas-cancel',
        ),
        re_path(
            r'^reservas/(?P<pk>\d+)/qr/?$',
            ReservationQRDetailView.as_view(),
            name=f'{name_prefix}reservas-qr-detail',
        ),
        re_path(
            r'^reservas/validar/?$',
            ValidateQRView.as_view(),
            name=f'{name_prefix}reservas-validate-qr',
        ),
        re_path(
            r'^reservas/simular-qr/?$',
            SimulateQRView.as_view(),
            name=f'{name_prefix}reservas-simulate-qr',
        ),
        re_path(
            r'^pagos/?$',
            ProcessPaymentView.as_view(),
            name=f'{name_prefix}pagos-process',
        ),
        re_path(
            r'^arrendador/cobros/?$',
            HostEarningsView.as_view(),
            name=f'{name_prefix}arrendador-cobros',
        ),
        re_path(
            r'^arrendador/cobros/retirar/?$',
            HostWithdrawView.as_view(),
            name=f'{name_prefix}arrendador-cobros-retirar',
        ),
    ]


urlpatterns = [
    path('', include(get_api_urlpatterns())),
    path('api/v1/', include(get_api_urlpatterns(name_prefix='v1-'))),
    path('admin/', admin.site.urls),
    path('', home_view, name='home'),
]
