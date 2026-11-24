# Plan de Tareas (TDD): Módulo 04 — Generación y Validación de Códigos QR

> **Módulo:** `04-qr-acceso`  
> **Especificación Asociada:** [spec.md](file:///c:/Users/gabra/Documents/ParkShare/ParkShare_Backend/specs/04-qr-acceso/spec.md)  
> **Enfoque Obligatorio:** TDD Estricto (**Rojo -> Verde -> Refactor -> Revisión**)  
> **Reglas Aplicables:** `docs/constitution.md`, `AGENTS.md`, `RN-02`, `RN-03`, `RN-06`

---

## Prerrequisitos de Arquitectura y Dependencias

- [x] **TASK-04-00: Verificar prerrequisitos de módulos base y DRF**
  - Confirmar existencia de modelos `Usuario` (con `placas`, `modelo_vehiculo`, `estado_cuenta`), `Cochera` y `Reserva` (`fecha_hora_inicio`, `fecha_hora_fin`) y sus servicios `start_reservation_usage` / `finish_reservation` de los Módulos `01-auth`, `02-cocheras` y `03-reservas`.
  - Confirmar si `djangorestframework` ya se encuentra autorizado en `requirements.txt` (en caso contrario, **detenerse y solicitar autorización al usuario** explicando su necesidad para endpoints REST, conforme a `AGENTS.md`).
  - Confirmar estructura de URLs y autenticación activa en `ParkShare_Backend`.

---

## Fase 1: Modelo `CodigoQR` y Migraciones (Rojo -> Verde)

- [x] **TASK-04-01 [ROJO]: Escribir tests de modelo y restricciones para `CodigoQR`**
  - Crear `ParkShare_Backend/tests/test_qr_models.py` (o en el paquete de pruebas correspondiente).
  - Cubrir los siguientes casos de prueba que deben **fallar inicialmente**:
    1. Creación válida de `CodigoQR` vinculado a una `Reserva` con `token_qr` SHA-256 (64 hex chars), `tipo_qr='ENTRADA'` y default `usado=False`.
    2. Rechazo de `tipo_qr` inválido distinto de `'ENTRADA'` o `'SALIDA'` mediante `full_clean()`.
    3. Rechazo de `token_qr` duplicado (`IntegrityError` por restricción `UNIQUE`).
    4. Rechazo de segundo `CodigoQR` con el mismo `(reserva_id, tipo_qr)` (`IntegrityError` por `uq_codigo_qr_reserva_tipo`).
    5. Validación de formato SHA-256 en `token_qr` (longitud != 64 o caracteres no hexadecimales) y validación `expiracion > fecha_emision` en `clean()`.
  - Ejecutar `python manage.py test` y verificar el fallo esperado (Fase Roja).

- [x] **TASK-04-02 [VERDE]: Implementar modelo `CodigoQR` y generar migración**
  - Definir el modelo `CodigoQR` en `ParkShare_Backend/models/` con `Meta.db_table = 'codigo_qr'` y columnas exactas del Diccionario de Datos (`id_qr`, `reserva`, `token_qr`, `tipo_qr`, `usado`, `fecha_emision`, `expiracion`).
  - Declarar `UniqueConstraint` en `('reserva', 'tipo_qr')`, índices en `token_qr` y `('reserva', 'tipo_qr', 'usado')`, y validaciones en `clean()`.
  - Ejecutar `python manage.py makemigrations` y `python manage.py migrate`.
  - Ejecutar `python manage.py test` sobre `test_qr_models` y verificar el 100% en Verde.

---

## Fase 2: Servicios de Generación, Validación y Cálculo de Retraso (`RN-02`, `RN-03`, `RN-06`)

- [x] **TASK-04-03 [ROJO]: Escribir tests unitarios de servicios de QR y reglas de negocio**
  - Crear `ParkShare_Backend/tests/test_qr_services.py` probando el módulo de servicio (antes de implementarlo):
    1. `issue_entry_qr(reserva)`: genera token SHA-256 único de 64 hex chars con entropía CSPRNG, `expiracion = reserva.fecha_hora_fin`, solo si `reserva.estado == 'CONFIRMADA'`; rechaza si la reserva está `SOLICITADA`, `CANCELADA`, `EXPIRADA` o `FINALIZADA`.
    2. `issue_entry_qr(reserva)` es idempotente si ya existe un QR de `ENTRADA` no usado, evitando duplicados.
    3. `calculate_late_checkout_penalty(fecha_hora_fin, hora_salida_real)`:
       - Salida antes de `fecha_hora_fin` -> `0 min`, `Decimal('0.00')`.
       - Salida a `fecha_hora_fin + 10 min 00 s` (`RN-02`) -> `0 min`, `Decimal('0.00')`.
       - Salida a `fecha_hora_fin + 10 min 01 s` -> `1 min`, `Decimal('10.00')`.
       - Salida a `fecha_hora_fin + 20 min 00 s` (`RN-03`) -> `10 min`, `Decimal('100.00')`.
    4. `validate_scanned_qr(token, host_user, now)`:
       - Flujo `ENTRADA`: invoca `start_reservation_usage` (`CONFIRMADA -> EN_USO`), marca QR entrada `usado=True`, crea QR `SALIDA` con `expiracion = max(reserva.fecha_hora_fin, now) + 24h`.
       - Flujo `SALIDA` (`RN-06`): invoca `finish_reservation` (`EN_USO -> FINALIZADA`), marca QR salida `usado=True`, guarda `hora_salida_real`, actualiza `reserva.monto_penalizacion` e invoca hook de ajuste de pago si `monto_penalizacion > 0`.
       - Rechazos: token no existente, `usado=True`, `now > expiracion`, reserva `CANCELADA` / `EXPIRADA`, llegada `> 15 min` antes de `fecha_hora_inicio`, y arrendador no propietario de la cochera.
  - Ejecutar `python manage.py test` y verificar el fallo esperado (Fase Roja).

- [x] **TASK-04-04 [VERDE + REFACTOR]: Implementar servicios criptográficos y transaccionales de QR**
  - Implementar en `ParkShare_Backend/services/qr_service.py` (y adaptador de reloj/token si aplica en `ParkShare_Backend/puentes/`):
    - Generación segura con `secrets.token_bytes(32)` + `hashlib.sha256(...).hexdigest()`.
    - Función pura `calculate_late_checkout_penalty(scheduled_end, actual_exit)`.
    - Operaciones atómicas con `@transaction.atomic` y `select_for_update()` sobre `CodigoQR` y `Reserva`.
    - Optimización ORM con `select_related('reserva__cochera', 'reserva__arrendatario')` para evitar consultas N+1.
  - Ejecutar `python manage.py test` y verificar que pasen en Verde; refactorizar manteniendo funciones pequeñas y sin duplicación.

---

## Fase 3: Serializadores y Endpoints REST (`GET /reservas/{id}/qr`, `POST /reservas/validar`)

- [x] **TASK-04-05 [ROJO]: Escribir tests de integración API (`APITestCase`) para endpoints QR**
  - Crear `ParkShare_Backend/tests/test_qr_api.py` cubriendo:
    1. `GET /reservas/{id}/qr`:
       - `200 OK` devolviendo QR `ENTRADA` cuando reserva está `CONFIRMADA` y usuario es el `ARRENDATARIO` dueño.
       - `200 OK` devolviendo QR `SALIDA` cuando reserva está `EN_USO`.
       - `401 Unauthorized` sin autenticación.
       - `403 Forbidden` si consulta otro arrendatario o un arrendador.
       - `404 Not Found` si no existe la reserva.
       - `409 Conflict` si la reserva está `SOLICITADA`, `CANCELADA` o `FINALIZADA`.
    2. `POST /reservas/validar`:
       - `200 OK` al validar `ENTRADA`: retorna datos de verificación (`nombre_completo`, `placas`, `modelo_vehiculo`, horario) sin exponer `token_qr` de `SALIDA`, marca `usado=True` y genera QR `SALIDA`.
       - `200 OK` al validar `SALIDA`: retorna `hora_salida_real`, `minutos_retraso`, `monto_penalizacion`, `monto_total`, y pasa reserva a `FINALIZADA`.
       - `400 Bad Request` por token faltante o formato no hexadecimal de 64 chars.
       - `403 Forbidden` si el usuario es `ARRENDATARIO`, `ARRENDADOR` pendiente (`RN-08`), o dueño de otra cochera.
       - `404 Not Found` por token inexistente.
       - `409 Conflict` por QR ya usado (`usado=True`), reserva cancelada o llegada demasiado anticipada.
       - `410 Gone` por QR expirado.
  - Ejecutar `python manage.py test` y verificar fallo inicial (Fase Roja).

- [x] **TASK-04-06 [VERDE + REFACTOR]: Implementar serializadores, permisos, vistas y rutas URL**
  - Crear serializadores DRF (`CodigoQRSerializer`, `ValidateQRInputSerializer`, `QRValidationResponseSerializer`) en `ParkShare_Backend/serializers/qr_serializers.py`.
  - Crear clases de permisos específicas (`IsRenterOwner`, `IsActiveVerifiedHost`) en `ParkShare_Backend/permissions.py` (o módulo de permisos compartido).
  - Implementar vistas (`ReservaQRDetailView`, `ValidateQRView`) en `ParkShare_Backend/views/qr_views.py` con mensajes de error claros en español y código en inglés.
  - Registrar rutas `reservas/<int:id_reserva>/qr` y `reservas/validar` en `ParkShare_Backend/urls.py`.
  - Ejecutar tests de API y refactorizar validaciones repetidas o consultas ORM.

---

## Fase 4: Verificación Final y Auditoría por el `Reviewer`

- [x] **TASK-04-07 [REVISIÓN]: Auditoría integral y suite completa de pruebas**
  - Ejecutar `python manage.py check` y `python manage.py makemigrations --check --dry-run`.
  - Ejecutar `python manage.py test` completo y certificar **0 errores y 0 fallos**.
  - Verificar checklist de auditoría para el **Reviewer**:
    - [x] Coincidencia exacta de tabla `codigo_qr` con el Diccionario de Datos.
    - [x] Reglas `RN-02`, `RN-03` y `RN-06` cubiertas con pruebas de frontera exacta (10 min 00 s vs 10 min 01 s vs 20 min 00 s).
    - [x] Tokens generados criptográficamente (SHA-256) y bloqueo transaccional contra doble escaneo.
    - [x] No exposición del token de `SALIDA` al arrendador en `POST /reservas/validar`.
    - [x] Consultas ORM optimizadas (`select_related`), código en inglés y mensajes en español.

---

## Fase 5: Conexión Frontend y Simulación de Escaneo QR (Rojo -> Verde -> Refactor -> Revisión)

- [x] **TASK-04-08 [ROJO]: Escribir tests de enriquecimiento retrocompatible para `GET /reservas/{id}/qr`**
  - Actualizar `ParkShare_Backend/tests/test_qr_api.py` (sin romper aserciones previas) para exigir en `GET /reservas/{id}/qr`:
    1. Presencia de `reserva.monto_base` (`str` decimal), `reserva.cochera` (`id_cochera`, `nombre`, `direccion`, `latitud` `float`, `longitud` `float`) y `reserva.arrendatario` (`id_usuario`, `nombre_completo`, `placas`, `modelo_vehiculo`).
    2. Mantenimiento de `assertNumQueries(3)` gracias al `select_related('arrendatario', 'cochera')` existente.
  - Ejecutar `python manage.py test ParkShare_Backend.tests.test_qr_api` y verificar el fallo esperado (Fase Roja).

- [x] **TASK-04-09 [VERDE + REFACTOR]: Enriquecer `QRReservationSummarySerializer` sin consultas extra**
  - Definir `QRReservationGarageSerializer` (`latitud` y `longitud` como `FloatField`) y `QRReservationTenantSerializer` en `ParkShare_Backend/serializers/qr_serializers.py`.
  - Incluir `monto_base`, `cochera` y `arrendatario` en `QRReservationSummarySerializer`.
  - Ejecutar `python manage.py test ParkShare_Backend.tests.test_qr_api` y verificar el pase en Verde con 3 consultas constantes.

- [x] **TASK-04-10 [ROJO]: Escribir tests unitarios de servicio para simulación de escaneo QR**
  - Ampliar `ParkShare_Backend/tests/test_qr_services.py` con pruebas para `list_active_qrs_for_simulation` y `simulate_qr_scan`:
    1. `list_active_qrs_for_simulation(host_user, qr_type=None, now=None)`:
       - Lista solo QRs activos (`usado=False`, `expiracion > now`) de cocheras del `host_user` en estados válidos (`CONFIRMADA + ENTRADA` o `EN_USO + SALIDA`), ordenados por `fecha_hora_inicio` ascendente.
       - Excluye QRs de otros arrendadores, usados, expirados o de reservas `SOLICITADA`/`CANCELADA`/`FINALIZADA`/`EXPIRADA`.
       - Filtra por `qr_type` (`ENTRADA`/`SALIDA`) y rechaza valores inválidos con `DomainValidationError`.
    2. `simulate_qr_scan(host_user, token=None, reserva_id=None, qr_type=None, respect_window=False, now=None)`:
       - Resuelve correctamente por `token`, por `reserva_id` + `qr_type`, solo `reserva_id`, solo `qr_type`, o selección automática sin argumentos.
       - Si `respect_window=False` y la reserva `ENTRADA` es futura (`now < fecha_hora_inicio - 15 min` y `now <= expiracion`), simula la entrada usando `effective_now = reserva.fecha_hora_inicio`, transiciona `CONFIRMADA -> EN_USO` y emite el QR `SALIDA` sin exponer su token.
       - Permite simular `SALIDA` inmediata tras la entrada simulada aplicando `RN-02`/`RN-03`/`RN-06` (`EN_USO -> FINALIZADA`).
       - Si `respect_window=True` en reserva futura, rechaza con `ConflictError`.
       - Mantiene `LateEntryCancelled` si `now > fecha_hora_inicio + 20 min`, `GoneError` si `now > expiracion`, `PermissionDeniedError` si la cochera pertenece a otro arrendador y `NotFoundError` si no existen QRs activos.
  - Ejecutar `python manage.py test ParkShare_Backend.tests.test_qr_services` y verificar fallo inicial (Fase Roja).

- [x] **TASK-04-11 [VERDE + REFACTOR]: Implementar servicios de listado y simulación QR**
  - Implementar `list_active_qrs_for_simulation` y `simulate_qr_scan` en `ParkShare_Backend/services/qr_service.py` reutilizando `validate_scanned_qr` y asegurando:
    - Bloqueo pesimista `select_for_update()` al resolver la reserva/QR candidato antes de validar.
    - Optimización ORM con `select_related('reserva__cochera', 'reserva__arrendatario')` sin N+1.
    - Retorno del resultado con `'simulado': True` sin duplicar lógica de transición ni cálculo de penalizaciones.
  - Ejecutar `python manage.py test ParkShare_Backend.tests.test_qr_services` en Verde y refactorizar helpers compartidos de validación de rol/token.

- [x] **TASK-04-12 [ROJO]: Escribir tests de integración API para `GET` y `POST /reservas/simular-qr`**
  - Ampliar `ParkShare_Backend/tests/test_qr_api.py` cubriendo:
    1. `GET /reservas/simular-qr` (y `/api/v1/reservas/simular-qr`):
       - `200 OK` paginado (`count`, `results`) con los 12 campos esperados por `MockQrDemoOption` (`id_qr`, `reserva_id`, `tipo_qr`, `token_qr`, `cochera_nombre`, `placas`, `modelo_vehiculo`, `nombre_arrendatario`, `estado_reserva`, `fecha_hora_inicio`, `fecha_hora_fin`, `expiracion`).
       - Filtro `?tipo_qr=ENTRADA` / `?tipo_qr=SALIDA` (`200 OK`) y `?tipo_qr=INVALIDO` (`400 Bad Request`).
       - `401 Unauthorized`, `403 Forbidden` para arrendatario / arrendador pendiente (`RN-08`), aislamiento entre arrendadores y `assertNumQueries` constante.
    2. `POST /reservas/simular-qr` (y `/api/v1/reservas/simular-qr`):
       - `200 OK` simulando `ENTRADA` en reserva futura (`{"tipo_qr": "ENTRADA"}`), actualizando `GET /reservas/simular-qr` al nuevo estado (`SALIDA`).
       - `200 OK` simulando `SALIDA` inmediata (`{"reserva_id": <id>}`) hasta `FINALIZADA`, devolviendo estructura `QrValidationResponse` + `"simulado": true`.
       - `200 OK` simulando por `{"token": "<sha256>"}`.
       - `400 Bad Request`, `401 Unauthorized`, `403 Forbidden` (cruzado / rol / `RN-08`), `404 Not Found` (`NoActiveQRToSimulate`), `409 Conflict` (`QRAlreadyUsed`, incompatibilidad `token`/`reserva_id`, `respetar_ventana: true`, `LateEntryCancelled`) y `410 Gone` (`QRExpired`).
  - Ejecutar `python manage.py test ParkShare_Backend.tests.test_qr_api` y verificar fallo inicial (Fase Roja).

- [x] **TASK-04-13 [VERDE + REFACTOR]: Implementar serializadores, vista `SimulateQRView` y ruta URL**
  - Añadir `ActiveSimulationQRSerializer` y `SimulateQRInputSerializer` en `ParkShare_Backend/serializers/qr_serializers.py`.
  - Implementar `SimulateQRView` (`GET` con `StandardPagination` y `POST`) en `ParkShare_Backend/views/qr_views.py` con permisos `[IsAuthenticated, IsActiveLandlord]` y manejo uniforme de excepciones (`handle_domain_exception`, `validation_error_response`).
  - Exportar `SimulateQRView` en `ParkShare_Backend/views/__init__.py` y registrar `re_path(r'^reservas/simular-qr/?$', ...)` en `ParkShare_Backend/urls.py` (incluyendo espejo `/api/v1/`).
  - Ejecutar `python manage.py test ParkShare_Backend.tests.test_qr_api` y refactorizar eliminando cualquier duplicación.

- [x] **TASK-04-14 [REVISIÓN]: Verificación final de Fase 5 y auditoría por el `Reviewer`**
  - Ejecutar `python manage.py check` y `python manage.py makemigrations --check --dry-run` (confirmar que no existen migraciones pendientes).
  - Ejecutar `python manage.py test` completo (todos los módulos) y certificar **0 errores y 0 fallos**.
  - Verificar checklist de auditoría para el **Reviewer**:
    - [x] Sin cambios al esquema de BD (`codigo_qr`), manteniendo fidelidad al Diccionario de Datos.
    - [x] Compatibilidad total de payload entre `GET /reservas/{id}/qr`, `GET /reservas/simular-qr`, `POST /reservas/simular-qr` y los componentes `QrScannerScreen.jsx`, `QrDisplayCard.jsx`, `ReservationQrScreen.jsx` y `src/types/qr.ts` de `ParkShareApp`.
    - [x] `POST /reservas/validar` conserva intactas sus validaciones estrictas de ventana horaria (`-15 min` / `+20 min`).
    - [x] Consultas ORM optimizadas (`select_related`, sin N+1), código en inglés y mensajes de usuario en español.
