# Plan de Tareas TDD — Módulo 02: Registro, Validación y Búsqueda Geográfica de Cocheras (`02-cocheras`)

> **Dependencia de módulo**: Requiere el Módulo `01-auth` implementado (`Usuario`, autenticación JWT y permisos `IsActiveAccount`, `IsActiveLandlord`, `IsAdminRole`). Seguir ciclo estricto **Rojo -> Verde -> Refactor -> Revisión**.

---

## Prerrequisitos y Autorizaciones (`AGENTS.md`)

- [x] **02.0.1 Confirmar autorización de archivos nuevos del módulo**
  - Confirmar creación de archivos de serializadores, servicios geoespaciales, vistas y pruebas para cocheras dentro de `ParkShare_Backend/` (sin dependencias externas adicionales a Django/DRF; la distancia Haversine se calcula con funciones ORM/SQL estándar de MySQL).

---

## Fase 1: Modelos y Restricciones de Base de Datos (TDD: Rojo -> Verde)

- [x] **02.1.1 (Rojo) Escribir tests de modelos `Cochera`, `FotoCochera` y `Disponibilidad`**
  - Crear `ParkShare_Backend/tests/test_cocheras_models.py` verificando:
    - Valores por defecto (`tipo_vehiculo='AMBOS'`, `estado_cochera='PENDIENTE'`), FK a `Usuario` (`arrendador`).
    - Validaciones de `latitud` (`[-90, 90]`), `longitud` (`[-180, 180]`), `metros_cuadrados > 0`, `espacios_totales >= 1`, `tarifa_hora > 0` (`RN-09`).
    - Validación de extensión de `FotoCochera.url_foto` (`.jpg`, `.jpeg`, `.png`, `.webp`).
    - Validación de `Disponibilidad`: `dia_semana` en `('L','M','X','J','V','S','D')` y `hora_fin > hora_inicio` (`CU-03`).
  - Ejecutar `python manage.py test` y comprobar fallo inicial (fase **Roja**).

- [x] **02.1.2 (Verde) Implementar modelos y migraciones de `cochera`, `foto_cochera` y `disponibilidad`**
  - Implementar `Cochera` (`db_table='cochera'`), `FotoCochera` (`db_table='foto_cochera'`) y `Disponibilidad` (`db_table='disponibilidad'`) con sus índices (`estado_cochera`, `(latitud, longitud)`, `arrendador`, `cochera`).
  - Generar migración `makemigrations` y aplicar `migrate`.
  - Ejecutar `test_cocheras_models.py` y confirmar paso al 100% (fase **Verde**).

---

## Fase 2: Servicios de Negocio, Validación Horaria y Búsqueda Haversine (TDD: Rojo -> Verde)

- [x] **02.2.1 (Rojo) Escribir tests unitarios de servicios de cochera (`RN-08`, `RN-09`, `RN-10`, `CU-04`)**
  - Crear `ParkShare_Backend/tests/test_cocheras_services.py` cubriendo:
    - Creación atómica de cochera + fotos + franjas de disponibilidad, validando ausencia de traslapes en el mismo `dia_semana` y estado inicial forzado a `PENDIENTE`.
    - Rechazo si el arrendador no tiene cuenta `ACTIVA` (`RN-08`).
    - Cálculo de distancia Haversine incluyendo coordenadas idénticas (`distancia_km == 0.0`) y distancias conocidas en CDMX.
    - Cálculo de `abierta_ahora` y `espacios_disponibles_ahora` en `America/Mexico_City` (y hook desacoplado para descuento de reservas activas y `calificacion_promedio`).
    - Máquina de estados: validación admin (`PENDIENTE -> APROBADA/RECHAZADA`, bloqueo de re-validación `409`), activación/inactivación por dueño (`APROBADA <-> INACTIVA`) y bloqueo de auto-aprobación (`PENDIENTE -> APROBADA`).
  - Verificar fallo inicial (fase **Roja**).

- [x] **02.2.2 (Verde + Refactor) Implementar `services/cochera_service.py` y consultas geoespaciales**
  - Implementar creación y actualización atómica con `transaction.atomic()`.
  - Implementar validación de no traslape de franjas horarias por `dia_semana`.
  - Implementar anotación SQL Haversine segura (con `LEAST`/`GREATEST` clamp en `[-1, 1]` para `ACOS`) y soporte para ordenamiento `cercania`, `precio` y `disponibilidad`.
  - Ejecutar `test_cocheras_services.py` y verificar paso al 100% (fase **Verde**), refactorizando helpers de validación.

---

## Fase 3: Serializadores y Endpoints REST (TDD: Rojo -> Verde -> Refactor)

- [x] **02.3.1 (Rojo) Escribir tests de integración API (`APITestCase`) para endpoints de cocheras**
  - Crear `ParkShare_Backend/tests/test_cocheras_api.py` cubriendo:
    - `POST /cocheras` (happy path `201`, cuenta arrendador `PENDIENTE`/`RECHAZADA` -> `403`, arrendatario -> `403`, payload inválido por `RN-09` o `CU-03` -> `400`).
    - `PATCH /admin/cocheras/{id}/validar` (`ADMIN` aprueba/rechaza -> `200`, no-admin -> `403`, rechazo sin motivo -> `400`, ya validada -> `409`).
    - `GET /cocheras?lat=&lng=&orden=` (filtra solo `APROBADA`, ordena por `cercania`, `precio`, `disponibilidad`, filtro `tipo_vehiculo` y `radio_km`, coordenadas faltantes/inválidas -> `400`).
    - `GET /cocheras/mis-cocheras` (devuelve solo cocheras del arrendador autenticado en cualquier estado).
    - `GET /cocheras/{id}` (visibilidad pública autenticada para `APROBADA`, restringida a dueño/admin para `PENDIENTE`/`INACTIVA`/`RECHAZADA` -> `404` a terceros).
    - `PATCH /cocheras/{id}` (dueño actualiza tarifa/horario y alterna `APROBADA <-> INACTIVA`, otro arrendador -> `403`, intento de auto-aprobar -> `403`/`409`).
    - Verificación de ausencia de N+1 queries con `assertNumQueries`.
  - Verificar fallo inicial (fase **Roja**).

- [x] **02.3.2 (Verde + Refactor) Implementar serializadores, vistas y rutas de cocheras**
  - Implementar serializadores anidados (`CocheraCreateSerializer`, `CocheraSearchResultSerializer`, `CocheraDetailSerializer`, `CocheraUpdateSerializer`, `CocheraAdminValidationSerializer`).
  - Implementar vistas DRF optimizadas con `select_related('arrendador')` y `prefetch_related('fotos', 'disponibilidades')`.
  - Registrar rutas en `urls.py` y modelos en `admin.py`.
  - Ejecutar `python manage.py test` y confirmar 100% de tests en verde.

- [x] **02.3.3 (Rojo) Escribir tests unitarios y de integración para `GET /admin/cocheras`**
  - Ampliar `ParkShare_Backend/tests/test_cocheras_services.py` con tests unitarios para `list_admin_garages` (default `PENDIENTE`, filtro `APROBADA`/`RECHAZADA`/`INACTIVA`/`TODOS`, ordenamiento `-id_cochera`, error en estado inválido y validación de rol `ADMIN`).
  - Ampliar `ParkShare_Backend/tests/test_cocheras_api.py` con tests de integración para `GET /admin/cocheras` cubriendo permisos (`401` sin token, `403` no-admin), filtros `estado`, paginación (`count`, `next`, `previous`, `results`), estructura completa de respuesta (`arrendador`, `foto_principal`, `fotos`, `disponibilidades`) y `assertNumQueries` sin N+1.
  - Ejecutar `python manage.py test` y comprobar fallo inicial (fase **Roja**).

- [x] **02.3.4 (Verde + Refactor) Implementar listado administrativo `GET /admin/cocheras`**
  - Implementar `list_admin_garages` en `services/cochera_service.py` con `select_related('arrendador')`, `prefetch_related('fotos', 'disponibilidades')` y ordenamiento `-id_cochera`.
  - Implementar `AdminGarageLandlordSerializer` y `AdminGarageReviewListSerializer` en `serializers/cochera_serializers.py`.
  - Implementar `AdminGarageListView` en `views/cochera_views.py` con paginación `StandardPagination` y registrar ruta `admin/cocheras` en `urls.py` (con espejo `/api/v1/`).
  - Ejecutar `python manage.py test` y confirmar 100% de tests en verde.

---

## Fase 4: Verificación Final y Auditoría

- [x] **02.4.1 Verificación integral y auditoría por `Reviewer`**
  - Ejecutar `python manage.py test` completo (Módulos 01 y 02) y `python manage.py makemigrations --check --dry-run`.
  - Entregar al **Reviewer** para auditar:
    - Esquema exacto de `cochera`, `foto_cochera`, `disponibilidad`.
    - Cumplimiento estricto de `RN-08`, `RN-09`, `RN-10` y `CU-02`, `CU-03`, `CU-04`.
    - Rendimiento de consultas geográficas y prevención de N+1.
    - Mensajes en español y código en inglés.
