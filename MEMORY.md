# MEMORY.md — Diario de Estudio
Memoria del proyecto entre sesiones. Máximo ~50 líneas: resume o elimina lo que ya no aporte.

## Estado actual
- Reglas de negocio RN-01 a RN-10 documentadas en `docs/constitution.md`.
- Roles de agentes definidos en `agents/` (`coordinator.md`, `planner.md`, `implementer.md`, `reviewer.md`).
- Diseñadas las 6 specs modulares y planes TDD (`spec.md` y `tasks.md`) en `specs/` (`01-auth` a `06-quejas-sanciones-resenas`).
- **Módulo `01-auth` implementado, probado y APROBADO por `Reviewer`** (35/35 tests en verde).
- **Módulo `02-cocheras` implementado, probado y APROBADO por `Reviewer`** (26 tests; 61/61 tests totales en verde incluyendo `GET /admin/cocheras`, sin migraciones pendientes).
- **Módulo `03-reservas` implementado, probado y APROBADO por `Reviewer`** (incluyendo Fase 5 con `RN-07` y `RN-08`; 20 tests).
- **Módulo `04-qr-acceso` implementado, probado y APROBADO por `Reviewer`** (incluyendo Fase 5 con `GET/POST /reservas/simular-qr` y enriquecimiento de `GET /reservas/{id}/qr`; 17 tests en verde).
- **Módulo `05-pagos-cobros` implementado, probado y APROBADO por `Reviewer`** (13 tests; 111/111 tests totales en verde, sin migraciones pendientes).

## Decisiones (y por qué)
- **Alineación con la Constitución**: Lógica de negocio en backend Django/DRF, código en inglés, mensajes/documentación en español, TDD obligatorio.
- **`AUTH_USER_MODEL = 'ParkShare_Backend.Usuario'`**: `Usuario` hereda de `AbstractBaseUser` y `PermissionsMixin` mapeado a `usuario` (`password` -> `db_column='password_hash'`).
- **Enrutamiento `/admin/...` y `/api/v1/`**: Se exponen rutas literales de las specs y espejo `/api/v1/...` antes de `admin.site.urls` sin colisión.
- **Autenticación JWT y `token_version`**: `CustomJWTAuthentication` valida firma, `token_type == 'access'`, `estado_cuenta != 'SUSPENDIDA'` y coincidencia de `token_version` en BD (`CU-11`).
- **Cocheras (`02-cocheras`) y Reservas (`03-reservas`)**: Búsqueda Haversine en SQL MySQL + `Subquery` de ocupación en 4 queries; `Reserva` (`RESTRICT`) con `select_for_update()` sobre `Cochera`, cálculo entero `_ceil_minutes` (`RN-01`..`RN-05`) y auto-emisión de QR de `ENTRADA` al pasar a `CONFIRMADA`.
- **Códigos QR (`04-qr-acceso`)**: `CodigoQR` (`db_table='codigo_qr'`, `UNIQUE(reserva_id, tipo_qr)`) con SHA-256 CSPRNG (`secrets`), `GoneError` (`410`), expiración de `SALIDA` extendida `+24h` (`max(fin, now)+24h`), validación atómica con `select_for_update()` sin filtrar token de `SALIDA` al arrendador, y hook `apply_late_checkout_payment_adjustment_hook` conectado con `05-pagos-cobros`.
- **Pagos y Cobros (`05-pagos-cobros`)**: `Pago` (`db_table='pago'`) y `RetiroCobro` (`db_table='retiro_cobro'`), patrón Bridge (`PaymentGatewayBridge` / `SimulatedPaymentGatewayAdapter` en `puentes/payment_gateway.py`), cobro atómico (`POST /pagos`: `201` confirma y emite QR, `402` cancela), sincronización de `RN-02`..`RN-05`, panel `GET /arrendador/cobros` en `America/Mexico_City` y retiro atómico `POST /arrendador/cobros/retirar`.

## Aprendizajes y errores a evitar
- En `Model.__init__`, Django `from_db` instancia `cls(*values)` con `_state.adding == True`; validar `not args and kwargs` antes de aplicar defaults por rol.
- En serializers `ListField` sin `child`, validar explícitamente `isinstance(..., str)` en servicios antes de `.strip()`.
- Al usar `prefetch_related`, iterar `.all()` en lugar de `.first()` para no romper la caché y evitar N+1.
- En `TestCase`, envolver pruebas de `IntegrityError` en `with transaction.atomic():` para no romper la transacción del test.
- Nunca ejecutar dos corridas paralelas de `manage.py test` sobre MySQL `test_parkshare_db` al mismo tiempo para evitar deadlocks.

## Próximos pasos
- Iniciar implementación TDD del Módulo `06-quejas-sanciones-resenas` (`specs/06-quejas-sanciones-resenas/spec.md` y `tasks.md`).
