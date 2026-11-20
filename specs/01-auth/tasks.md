# Plan de Tareas TDD — Módulo 01: Autenticación, Usuarios y Administración de Cuentas (`01-auth`)

> **Regla de Oro**: Ninguna línea de código de producción se escribe sin una prueba automatizada fallida previa (**Rojo -> Verde -> Refactor -> Revisión**). Todos los identificadores en código van en inglés; los mensajes de API/error van en español.

---

## Prerrequisitos y Autorizaciones (`AGENTS.md`)

- [x] **01.0.1 Solicitar autorización de dependencias, archivos nuevos y esquema base**
  - Confirmar con el usuario/Coordinador antes de editar `requirements.txt` o crear archivos:
    1. Añadir `djangorestframework` (para serializadores, vistas API, permisos y `APITestCase` del backend REST).
    2. Añadir `djangorestframework-simplejwt` (para emisión y validación de JWT en `CU-01` e invalidación vía `token_version` / blacklist en `CU-11`).
    3. Confirmar creación de estructura modular dentro de `ParkShare_Backend/` (`serializers/`, `services/`, `permissions.py`, `tests/`).
    4. Confirmar resolución del conflicto de rutas `/admin/...` vs `django.contrib.admin` en `ParkShare_Backend/urls.py` (prefijo `/api/v1/` vs reubicar admin nativo).
    5. Confirmar configuración de `AUTH_USER_MODEL = 'ParkShare_Backend.Usuario'` antes de generar la migración `0001_initial`.

---

## Fase 1: Modelos y Validaciones de Dominio (TDD: Rojo -> Verde)

- [x] **01.1.1 (Rojo) Escribir tests de modelos y restricciones del Diccionario de Datos**
  - Crear `ParkShare_Backend/tests/test_auth_models.py` probando:
    - Creación de `Usuario` (`ARRENDATARIO` con `estado_cuenta='ACTIVA'`, `ARRENDADOR` con `estado_cuenta='PENDIENTE'`), hashing de contraseña (`password_hash`) y `token_version=1`.
    - Unicidad de `correo` (case-insensitive) y restricción de edad mínima (18 años cumplidos hoy pasa; 17 años 364 días falla).
    - Validación alfabética de `nombre_completo`, formato numérico 10 dígitos / E.164 en `telefono`, y `@gmail.com` obligatorio para `ARRENDADOR` (`RN-07`).
    - Creación de `DocumentoArrendador` (`estado_verificacion='PENDIENTE'`, validación de extensiones `.pdf`/imagen para INE y `.pdf` para CSF y propiedad) y regla de `motivo_rechazo` obligatorio al rechazar.
    - Creación de `SuspensionCuenta` con FK `usuario` y `admin`, `estado='ACTIVA'` y `fecha_suspension`.
  - Ejecutar `python manage.py test` y verificar que los tests fallen por ausencia de modelos (fase **Roja**).

- [x] **01.1.2 (Verde) Implementar modelos `Usuario`, `DocumentoArrendador`, `SuspensionCuenta` y migraciones**
  - Definir `UsuarioManager`, `Usuario` (`db_table='usuario'`), `DocumentoArrendador` (`db_table='documento_arrendador'`) y `SuspensionCuenta` (`db_table='suspension_cuenta'`) en `ParkShare_Backend/models.py` (o paquete `models/`), respetando exactamente nombres de tablas, columnas, tipos, enums, nulidades e índices de `spec.md`.
  - Configurar `AUTH_USER_MODEL` y `REST_FRAMEWORK` / `SIMPLE_JWT` en `ParkShare_Backend/settings.py`.
  - Generar migración `python manage.py makemigrations` y aplicar `python manage.py migrate`.
  - Ejecutar `test_auth_models.py` y confirmar paso al 100% (fase **Verde**).

---

## Fase 2: Lógica de Negocio, Servicios y Permisos (TDD: Rojo -> Verde)

- [x] **01.2.1 (Rojo) Escribir tests unitarios de servicios y reglas `RN-07`, `RN-08`, `CU-10`, `CU-11`**
  - Crear `ParkShare_Backend/tests/test_auth_services.py` cubriendo:
    - Servicio de registro (`register_user`): alta atómica de arrendador + documentos (`PENDIENTE`), rechazo de rol `ADMIN`, validación de vehículo en arrendatario.
    - Servicio de verificación (`review_landlord_request`): aprobación activa la cuenta (`ACTIVA`) y `APROBADO`; rechazo guarda `motivo_rechazo` (incluyendo `"Otro"` + detalle) y pasa usuario a `RECHAZADA`; bloquea re-evaluación (`409`).
    - Servicio de suspensión (`suspend_account`): registra `SuspensionCuenta`, cambia `estado_cuenta='SUSPENDIDA'`, incrementa `token_version`, bloquea doble suspensión y auto-suspensión del admin.
    - Clases de permisos: `IsAdminRole`, `IsActiveAccount`, `IsActiveLandlord`, `IsActiveTenant`, y autenticación JWT verificando `token_version` y estado no suspendido.
  - Ejecutar tests y verificar fallo inicial (fase **Roja**).

- [x] **01.2.2 (Verde + Refactor) Implementar validadores, servicios de dominio, autenticación JWT y permisos**
  - Implementar validadores reutilizables (alfabético, teléfono, mayoría de edad, Gmail, extensiones documentales).
  - Implementar `services/auth_service.py` con `transaction.atomic()` para registro de arrendador, resolución de solicitudes (`CU-10`) y suspensión de cuentas (`CU-11`).
  - Implementar `CustomJWTAuthentication` que verifique `usuario.estado_cuenta != 'SUSPENDIDA'` y coincidencia exacta de `token_version`.
  - Implementar clases de permiso en `permissions.py`.
  - Ejecutar `test_auth_services.py` y confirmar paso al 100% (fase **Verde**), refactorizando duplicaciones sin romper tests.

---

## Fase 3: Serializadores y Endpoints REST (TDD: Rojo -> Verde -> Refactor)

- [x] **01.3.1 (Rojo) Escribir tests de integración API (`APITestCase`) para registro y login (`CU-01`, `RN-07`)**
  - Crear `ParkShare_Backend/tests/test_auth_api.py` cubriendo:
    - `POST /auth/registro` (felices + todos los casos de error `400`).
    - `POST /auth/login` (`200` con perfil y JWT, `401` credenciales inválidas, `403` si cuenta `SUSPENDIDA`, y `200` con estado `PENDIENTE`/`RECHAZADA` + `motivo_rechazo`, bloqueado en operaciones por `IsActiveAccount`).
  - Verificar fallo inicial (fase **Roja**).

- [x] **01.3.2 (Verde) Implementar serializadores, vistas y rutas de registro/login**
  - Implementar serializadores y vistas DRF para `POST /auth/registro` y `POST /auth/login` con respuestas y códigos HTTP exactos de `spec.md`.
  - Registrar rutas en `urls.py`.
  - Ejecutar `test_auth_api.py` y confirmar paso al 100% (fase **Verde**).

- [x] **01.3.3 (Rojo) Escribir tests de integración API para endpoints administrativos (`CU-10`, `CU-11`)**
  - Ampliar `test_auth_api.py` (o crear `test_admin_accounts_api.py`) cubriendo:
    - `GET /admin/solicitudes` y `GET /admin/solicitudes/{id}` (permisos `ADMIN`, `403` para no-admin, filtro `PENDIENTE`, `404` inexistente, verificación de queries `select_related` sin N+1).
    - `PATCH /admin/solicitudes/{id}` (aprobar, rechazar con motivos catalogados y `"Otro"` + detalle, `400` sin motivo, `409` ya resuelta).
    - `GET /admin/cuentas?q=` y `GET /admin/cuentas/{id}` (búsqueda por nombre/correo, estructura de `resumen_disciplinario` e historiales).
    - `POST /admin/cuentas/{id}/suspender` (suspensión efectiva + verificación en vivo de que el token JWT anterior del usuario suspendido deja de funcionar inmediatamente y su login posterior retorna `403`).
  - Verificar fallo inicial (fase **Roja**).

- [x] **01.3.4 (Verde + Refactor) Implementar serializadores, vistas y rutas administrativas (`CU-10`, `CU-11`)**
  - Implementar vistas DRF optimizadas con `select_related('arrendador')` y `prefetch_related` para listado/detalle de solicitudes y cuentas.
  - Registrar modelos en `ParkShare_Backend/admin.py` si aplica.
  - Ejecutar toda la suite `python manage.py test` y refactorizar manteniendo 100% de tests en verde.

---

## Fase 4: Verificación Final y Auditoría

- [x] **01.4.1 Verificación completa de la suite y auditoría por `Reviewer`**
  - Ejecutar `python manage.py test` completo y validar 0 fallos/errores y 0 migraciones pendientes (`python manage.py makemigrations --check --dry-run`).
  - Entregar al **Reviewer** para auditar:
    - Coincidencia exacta de tablas, columnas, tipos y enums contra `spec.md`.
    - Reglas `RN-07` y `RN-08` (edad, Gmail, documentos, activación solo vía `CU-10`, bloqueo de sesiones tras `CU-11`).
    - Idioma: código en inglés y mensajes de error/respuesta en español.
    - Ausencia de consultas N+1 y uso exclusivo de dependencias autorizadas.
