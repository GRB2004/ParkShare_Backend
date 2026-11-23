# Plan de Tareas TDD — Módulo 03: Reservaciones, Máquina de Estados y Penalizaciones (`03-reservas`)

> **Dependencia de módulos**: Requiere `01-auth` (`Usuario`, JWT, permisos) y `02-cocheras` (`Cochera`, `Disponibilidad`). Seguir estrictamente **Rojo -> Verde -> Refactor -> Revisión**.

---

## Prerrequisitos y Autorizaciones (`AGENTS.md`)

- [x] **03.0.1 Confirmar autorización de archivos nuevos del módulo**
  - Confirmar creación de archivos de serializadores, servicios de reserva/penalizaciones, vistas y pruebas dentro de `ParkShare_Backend/` (sin nuevas dependencias externas; `math`, `decimal` y `django.utils.timezone` son estándar).

---

## Fase 1: Modelo `Reserva` y Migraciones (TDD: Rojo -> Verde)

- [x] **03.1.1 (Rojo) Escribir tests del modelo `Reserva` y restricciones**
  - Crear `ParkShare_Backend/tests/test_reservas_models.py` verificando:
    - Valores por defecto (`estado='SOLICITADA'`, `monto_penalizacion=Decimal('0.00')`, `hora_salida_real=None`, `fecha_creacion` automática).
    - Claves foráneas `arrendatario -> Usuario` y `cochera -> Cochera`.
    - Precisión decimal (`DECIMAL(8, 2)`) en `monto_base` y `monto_penalizacion`, y catálogo completo de `ENUM` de `estado`.
  - Ejecutar `python manage.py test` y comprobar fallo inicial (fase **Roja**).

- [x] **03.1.2 (Verde) Implementar modelo `Reserva` e índices compuestos y migrar**
  - Implementar `Reserva` (`db_table='reserva'`) en `models` respetando exactamente columnas, tipos, nulidades e índices compuestos definidos en `spec.md`.
  - Generar migración `makemigrations` y aplicar `migrate`.
  - Ejecutar `test_reservas_models.py` y confirmar paso al 100% (fase **Verde**).

---

## Fase 2: Lógica de Negocio, Máquina de Estados y Reglas `RN-01` a `RN-05` (TDD: Rojo -> Verde)

- [x] **03.2.1 (Rojo) Escribir tests unitarios de servicios de reserva y penalizaciones**
  - Crear `ParkShare_Backend/tests/test_reservas_services.py` cubriendo:
    - Validación `RN-01`: 30 min, 90 min, 24 h pasan; 15 min, 45 min, 24 h 30 min, `fin <= inicio` fallan.
    - Validación contra `Disponibilidad` (mismo día y cruce de medianoche; fallo si está fuera de horario).
    - Control de cupo con traslapes (`[10:00,11:00)` y `[11:00,12:00)` contiguas válidas; traslape sobre saturación falla; reservas `CANCELADA`/`EXPIRADA` no consumen cupo) y cálculo exacto de `monto_base`.
    - Transiciones válidas y prohibidas de máquina de estados (`SOLICITADA`, `CONFIRMADA`, `EN_USO`, `FINALIZADA`, `CANCELADA`, `EXPIRADA`).
    - Cálculo exacto `RN-02` y `RN-03`: salida en `+10m00s` (`$0.00`), `+10m01s` (`$10.00`), `+20m00s` (`$100.00`).
    - Cálculo exacto `RN-04` y `RN-05`: cancelación en `CONFIRMADA` a los `10m00s` (`$0.00`), a los `10m01s` (`$100.00`), y cancelación en `SOLICITADA` (`$0.00`).
    - Actualización del cálculo de `espacios_disponibles_ahora` en el servicio de `02-cocheras` considerando reservas activas vigentes.
  - Verificar fallo inicial (fase **Roja**).

- [x] **03.2.2 (Verde + Refactor) Implementar `services/reserva_service.py` y conector de ocupación en `02-cocheras`**
  - Implementar funciones puras de cálculo (`validate_reservation_duration`, `calculate_base_amount`, `calculate_late_surcharge`, `calculate_cancellation_penalty`) y funciones transaccionales (`create_reservation` con `transaction.atomic()` + `select_for_update()`, `transition_reservation_state`, `cancel_reservation`, `finish_reservation`).
  - Conectar el conteo de reservas activas (`SOLICITADA`, `CONFIRMADA`, `EN_USO`) en el helper de disponibilidad de `cochera_service.py` (Módulo `02-cocheras`).
  - Ejecutar `test_reservas_services.py` y la suite previa de `02-cocheras` confirmando 100% en verde (fase **Verde**), refactorizando duplicaciones.

---

## Fase 3: Serializadores y Endpoints REST (TDD: Rojo -> Verde -> Refactor)

- [x] **03.3.1 (Rojo) Escribir tests de integración API (`APITestCase`) para endpoints de reservas**
  - Crear `ParkShare_Backend/tests/test_reservas_api.py` cubriendo:
    - `POST /reservas`: happy path `201`, cochera `PENDIENTE`/`INACTIVA` -> `409`, cupo saturado -> `409`, `RN-01` inválido -> `400`, fuera de horario -> `400`, auto-reserva -> `400`/`403`, arrendador intentando reservar -> `403`.
    - `GET /reservas`: vistas `proximas`/`anteriores` para `ARRENDATARIO` y `proximas_llegadas`/`en_uso`/`historial` para `ARRENDADOR`, aislamiento entre usuarios y verificación sin N+1 (`assertNumQueries`).
    - `GET /reservas/{id}`: acceso permitido solo a arrendatario dueño, arrendador dueño o admin (`403` para terceros), cálculo de `tiempo_en_uso` y `recargo_estimado_actual` cuando está `EN_USO`.
    - `POST /reservas/{id}/cancelar`: aplicación de `RN-04` (`$0.00`) y `RN-05` (`$100.00`), rechazo `409` en `EN_USO`/`FINALIZADA`/`CANCELADA`, rechazo `403` para no-dueños.
    - `PATCH /reservas/{id}`: transiciones válidas por rol (`ARRENDADOR` inicia `EN_USO` y finaliza `FINALIZADA` aplicando `RN-02`/`RN-03`), bloqueo `403` si `ARRENDATARIO` intenta confirmar/finalizar, bloqueo `409` en transiciones inválidas de máquina de estados.
    - Test de concurrencia controlada simulando reserva simultánea del último cajón (`select_for_update`).
  - Verificar fallo inicial (fase **Roja**).

- [x] **03.3.2 (Verde + Refactor) Implementar serializadores, vistas y rutas de reservas**
  - Implementar serializadores (`ReservaCreateSerializer`, `ReservaListSerializer`, `ReservaDetailSerializer`, `ReservaEstadoUpdateSerializer`) y vistas DRF con `select_related('cochera', 'arrendatario')`.
  - Registrar rutas en `urls.py` y `Reserva` en `admin.py`.
  - Ejecutar `python manage.py test` y verificar 100% de tests en verde.

---

## Fase 4: Verificación Final y Auditoría

- [x] **03.4.1 Verificación completa de la suite y auditoría por `Reviewer`**
  - Ejecutar la suite integral completa (`python manage.py test`) para los módulos `01-auth`, `02-cocheras` y `03-reservas` y validar `python manage.py makemigrations --check --dry-run`.
  - Entregar al **Reviewer** para auditar:
    - Cumplimiento exacto de `RN-01` a `RN-05` y trazabilidad con `RN-06`.
    - Integridad de la máquina de estados y control de acceso por rol en cada transición.
    - Concurrencia segura en creación de reservas y ausencia de consultas N+1.
    - Código en inglés, mensajes al usuario en español y diccionario de datos exacto.
