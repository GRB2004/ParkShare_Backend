# Plan de Tareas (TDD): Módulo 06 — Reseñas, Quejas, Sanciones y Apelaciones

> **Módulo:** `06-quejas-sanciones-resenas`  
> **Especificación Asociada:** [spec.md](file:///c:/Users/gabra/Documents/ParkShare/ParkShare_Backend/specs/06-quejas-sanciones-resenas/spec.md)  
> **Enfoque Obligatorio:** TDD Estricto (**Rojo -> Verde -> Refactor -> Revisión**)  
> **Reglas Aplicables:** `docs/constitution.md`, `AGENTS.md`, `CU-09`, `CU-12`, `CU-13`

---

## Prerrequisitos de Arquitectura y Dependencias

- [ ] **TASK-06-00: Verificar prerrequisitos de módulos base y DRF**
  - Confirmar modelos `Usuario`, `Cochera` y `Reserva` (`fecha_hora_inicio`, `fecha_hora_fin`, `estado` incluyendo `'EXPIRADA'`) de los Módulos `01-auth`, `02-cocheras` y `03-reservas`, y el flujo `'FINALIZADA'` emitido por `04-qr-acceso`.
  - Confirmar estrategia de enrutamiento para `/admin/...` (`api/v1/` vs `/django-admin/`) alineada con `01-auth` y `02-cocheras`.
  - Confirmar presencia/autorización de `djangorestframework` en `requirements.txt` (en caso contrario, preguntar al usuario antes de agregar dependencias).

---

## Fase 1: Modelos (`Resena`, `Queja`, `Sancion`, `Apelacion`) y Migraciones (Rojo -> Verde)

- [ ] **TASK-06-01 [ROJO]: Escribir tests de modelos, restricciones y enums**
  - Crear `ParkShare_Backend/tests/test_claims_reviews_models.py` con casos que deben **fallar inicialmente**:
    1. `Resena`: creación válida (`calificacion` 1..5), rechazo por `CheckConstraint`/`clean()` si `calificacion < 1` o `> 5`, rechazo si `autor == destinatario`, y `IntegrityError` por `UNIQUE(reserva_id, autor_id)`.
    2. `Queja`: creación válida con default `estado='PENDIENTE'` y rechazo de valores fuera de `motivo_categoria` (`TRATO_INADECUADO`, `NO_PUDE_ACCEDER`, `DISTINTA_A_PUBLICADO`, `OTRO`) y `estado`.
    3. `Sancion`: creación válida vinculada 1:1 a `Queja` con default `estado='VIGENTE'`, e `IntegrityError` al intentar asociar dos sanciones a la misma `Queja`.
    4. `Apelacion`: creación válida vinculada 1:1 a `Sancion` con `url_evidencia` nullable y default `estado='EN_REVISION'`, e `IntegrityError` al intentar duplicar apelación para una misma `Sancion`.
  - Ejecutar `python manage.py test` y verificar fallo inicial (Fase Roja).

- [ ] **TASK-06-02 [VERDE]: Implementar modelos y ejecutar migraciones**
  - Definir `Resena` (`db_table = 'resena'`), `Queja` (`db_table = 'queja'`), `Sancion` (`db_table = 'sancion'`) y `Apelacion` (`db_table = 'apelacion'`) en `ParkShare_Backend/models/` respetando exactamente nombres de columnas, tipos SQL, nulidad, `UniqueConstraint` y `CheckConstraint`.
  - Ejecutar `python manage.py makemigrations` y `python manage.py migrate`.
  - Ejecutar `python manage.py test` sobre `test_claims_reviews_models` y verificar el 100% en Verde.

---

## Fase 2: Servicios de Dominio y Lógica Transaccional (`CU-09`, `CU-12`, `CU-13`)

- [ ] **TASK-06-03 [ROJO]: Escribir tests de servicios para reseñas, quejas, sanciones y apelaciones**
  - Crear `ParkShare_Backend/tests/test_claims_reviews_services.py` cubriendo:
    1. `create_review(reserva, author, calificacion, etiquetas, comentario)`:
       - Deriva `destinatario` automáticamente (arrendatario <-> arrendador).
       - Permite máximo 2 reseñas por reserva (1 por participante).
       - Rechaza si `reserva.estado != 'FINALIZADA'`, si `author` no es participante, o si ya reseñó.
    2. `create_complaint(reserva, renter, motivo_categoria, descripcion)`:
       - Solo acepta reservas en `CONFIRMADA`, `EN_USO` o `FINALIZADA`.
       - Asigna automáticamente `arrendador = reserva.cochera.arrendador` y `estado = 'PENDIENTE'`.
    3. `resolve_complaint(complaint, admin_user, new_status, motivo_sancion)`:
       - `'DESCARTADA'`: cambia estado de queja sin crear sanción.
       - `'REVISADA'`: exige `motivo_sancion`, actualiza queja y crea `Sancion(estado='VIGENTE')` atómicamente.
       - Rechaza resolver una queja que ya no está `'PENDIENTE'`.
    4. `submit_appeal(sanction, host_user, descripcion, url_evidencia)`:
       - Solo sobre sanción `'VIGENTE'` perteneciente al arrendador y sin apelación previa.
    5. `resolve_appeal(appeal, admin_user, new_status)`:
       - `'ACEPTADA'`: marca apelación `'ACEPTADA'` y transiciona `sancion.estado` a `'RETIRADA'`.
       - `'RECHAZADA'`: marca apelación `'RECHAZADA'` y mantiene `sancion.estado == 'VIGENTE'`.
       - Rechaza resolver una apelación que ya no está `'EN_REVISION'`.
  - Ejecutar `python manage.py test` y verificar fallo inicial (Fase Roja).

- [ ] **TASK-06-04 [VERDE + REFACTOR]: Implementar capa de servicios transaccionales**
  - Implementar `ParkShare_Backend/services/review_service.py` y `ParkShare_Backend/services/claim_sanction_service.py` con `@transaction.atomic` y `select_for_update()` en transiciones de `Queja`, `Sancion` y `Apelacion`.
  - Optimizar consultas ORM con `select_related` (`reserva__cochera__arrendador`, `arrendatario`, `arrendador`, `sancion__apelacion`, `queja`).
  - Ejecutar `python manage.py test` en Verde y refactorizar eliminando duplicaciones entre validaciones de estado.

---

## Fase 3: Serializadores, Permisos y Endpoints REST (9 Endpoints)

- [ ] **TASK-06-05 [ROJO]: Escribir tests de integración API (`APITestCase`) para Reseñas y Quejas**
  - Crear `ParkShare_Backend/tests/test_reviews_complaints_api.py` cubriendo:
    1. `POST /reservas/{id}/resenas`: `201 Created` para ambos participantes en `FINALIZADA`, `400` por calificación inválida / `etiquetas` > 255, `403` por tercero, `404` inexistente, `409` por estado no finalizado o reseña duplicada, e ignorar `autor_id`/`destinatario_id` del payload.
    2. `POST /quejas`: `201 Created` en estados `CONFIRMADA`, `EN_USO` y `FINALIZADA`; `400` por categoría o descripción < 10 chars; `403` por usuario no arrendatario o reserva ajena; `409` en estado `SOLICITADA` o `CANCELADA`.
  - Ejecutar `python manage.py test` y verificar fallo inicial (Fase Roja).

- [ ] **TASK-06-06 [ROJO]: Escribir tests de integración API (`APITestCase`) para Admin, Sanciones y Apelaciones**
  - Crear `ParkShare_Backend/tests/test_sanctions_appeals_api.py` cubriendo:
    1. `GET /admin/quejas`: `200 OK` devolviendo `quejas_pendientes` y `apelaciones_en_revision` (más filtros opcionales); `403` para no-admin; verificación de eficiencia con `assertNumQueries`.
    2. `GET /admin/quejas/{id}`: `200 OK` con detalle completo y conteo de `sanciones_vigentes`; `403`/`404`.
    3. `PATCH /admin/quejas/{id}`: `200 OK` para `DESCARTADA` y `REVISADA` (creando `Sancion`), `400` si falta `motivo_sancion`, `409` si ya resuelta.
    4. `GET /arrendador/sanciones` y `GET /arrendador/sanciones/{id}`: `200 OK` solo para el arrendador dueño (listando `VIGENTE` y `RETIRADA`); `403` para otro arrendador o rol distinto.
    5. `POST /sanciones/{id}/apelar`: `201 Created` (con y sin `url_evidencia`), `400` por descripción corta/URL inválida, `403` por otro arrendador, `409` si sanción `RETIRADA` o ya apelada.
    6. `PATCH /admin/apelaciones/{id}`: `200 OK` con `ACEPTADA` (`sancion_estado='RETIRADA'`) y `RECHAZADA` (`sancion_estado='VIGENTE'`), `400` por estado inválido, `403` no-admin, `409` si ya resuelta.
  - Ejecutar `python manage.py test` y verificar fallo inicial (Fase Roja).

- [ ] **TASK-06-07 [VERDE + REFACTOR]: Implementar serializadores, vistas y URLs del Módulo 06**
  - Crear serializadores DRF en `ParkShare_Backend/serializers/claims_reviews_serializers.py`.
  - Crear vistas DRF en `ParkShare_Backend/views/claims_reviews_views.py` con clases de permisos explícitas (`IsAdminRole`, `IsHostRole`, `IsRenterRole`, `IsReservationParticipant`) y mensajes en español.
  - Registrar las 9 rutas en `ParkShare_Backend/urls.py`:
    - `reservas/<int:id_reserva>/resenas`
    - `quejas`
    - `admin/quejas`
    - `admin/quejas/<int:id_queja>`
    - `arrendador/sanciones`
    - `arrendador/sanciones/<int:id_sancion>`
    - `sanciones/<int:id_sancion>/apelar`
    - `admin/apelaciones/<int:id_apelacion>`
  - Ejecutar tests API en Verde y refactorizar minimizando consultas ORM y código repetido.

---

## Fase 4: Verificación Final y Auditoría por el `Reviewer`

- [ ] **TASK-06-08 [REVISIÓN]: Auditoría integral y suite completa de pruebas**
  - Ejecutar `python manage.py check` y `python manage.py makemigrations --check --dry-run`.
  - Ejecutar `python manage.py test` completo y certificar **0 errores y 0 fallos**.
  - Verificar checklist de auditoría para el **Reviewer**:
    - [ ] Coincidencia exacta de las 4 tablas (`resena`, `queja`, `sancion`, `apelacion`) con el Diccionario de Datos.
    - [ ] Restricción `UNIQUE(reserva_id, autor_id)` garantizando máximo 2 reseñas por reserva y validación de estado `FINALIZADA`.
    - [ ] Autorización estricta por rol (`ARRENDATARIO`, `ARRENDADOR`, `ADMIN`) y propiedad del recurso en los 9 endpoints.
    - [ ] Transiciones atómicas en `PATCH /admin/quejas/{id}` y `PATCH /admin/apelaciones/{id}` sin N+1 (`select_related`).
    - [ ] Código e identificadores en inglés y respuestas/mensajes en español.
