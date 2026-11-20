# MEMORY.md — Diario de Estudio
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- Reglas de negocio RN-01 a RN-10 documentadas en `docs/constitution.md`.
- Roles de agentes definidos en `agents/` (`coordinator.md`, `planner.md`, `implementer.md`, `reviewer.md`).
- Diseñadas las 6 specs modulares y planes TDD (`spec.md` y `tasks.md`) en `specs/` (`01-auth` a `06-quejas-sanciones-resenas`).
- **Módulo `01-auth` implementado, probado y APROBADO por `Reviewer`** (35/35 tests en verde).
- **Módulo `02-cocheras` implementado, probado y APROBADO por `Reviewer`** (24 tests nuevos; 59/59 tests totales en verde, sin migraciones pendientes).

## Decisiones (y por qué)
- **Alineación con la Constitución**: Lógica de negocio en backend Django/DRF, código en inglés, mensajes/documentación en español, TDD obligatorio.
- **`AUTH_USER_MODEL = 'ParkShare_Backend.Usuario'`**: `Usuario` hereda de `AbstractBaseUser` y `PermissionsMixin` mapeado a `usuario` (`password` -> `db_column='password_hash'`).
- **Enrutamiento `/admin/...` y `/api/v1/`**: Se exponen rutas literales de las specs (`/auth/...`, `/cocheras/...`, `/admin/...`) y espejo `/api/v1/...` antes de `admin.site.urls` sin colisión.
- **Autenticación JWT y `token_version`**: `CustomJWTAuthentication` valida firma, `token_type == 'access'`, `estado_cuenta != 'SUSPENDIDA'` y coincidencia de `token_version` en BD para invalidación instantánea en `CU-11`.
- **Cocheras (`02-cocheras`)**: `cochera` conserva exactamente 11 columnas (`motivo` de rechazo se devuelve en JSON de `/admin/cocheras/{id}/validar`). Búsqueda Haversine en SQL MySQL (`Cast` + `ACos` con clamp `[-1, 1]`), hooks desacoplados `get_active_reservations_count` (`03-reservas`) y `get_garage_rating_summary` (`06-quejas-sanciones-resenas`), sin nuevas dependencias.

## Aprendizajes y errores a evitar
- En `Model.__init__`, Django `from_db` instancia `cls(*values)` con `_state.adding == True`; validar `not args and kwargs` antes de aplicar defaults por rol.
- En serializers `ListField` sin `child`, validar explícitamente `isinstance(..., str)` en servicios antes de `.strip()` para evitar `AttributeError` (`500`).
- Al usar `prefetch_related('fotos')`, iterar `.all()` en lugar de `.first()` para no romper la caché y evitar consultas N+1.
- Truncar microsegundos (`replace(microsecond=0)`) al comparar hora actual contra `hora_fin = 23:59:59`.

## Próximos pasos
- Iniciar implementación TDD del Módulo `03-reservas` (`specs/03-reservas/spec.md` y `tasks.md`).
