# Plan de Tareas (TDD): Módulo 05 — Pagos, Cobros y Liquidaciones

> **Módulo:** `05-pagos-cobros`  
> **Especificación Asociada:** [spec.md](file:///c:/Users/gabra/Documents/ParkShare/ParkShare_Backend/specs/05-pagos-cobros/spec.md)  
> **Enfoque Obligatorio:** TDD Estricto (**Rojo -> Verde -> Refactor -> Revisión**)  
> **Reglas Aplicables:** `docs/constitution.md`, `AGENTS.md`, `RN-02`, `RN-03`, `RN-04`, `RN-05`, `CU-06`, `CU-07`

---

## Prerrequisitos de Arquitectura y Dependencias

- [ ] **TASK-05-00: Verificar prerrequisitos de módulos base, QR y DRF**
  - Confirmar modelos `Usuario` (con campo `cuenta_cobro` y `estado_cuenta`), `Cochera`, `Reserva` (con `fecha_hora_inicio`, `fecha_hora_fin` y servicio `confirm_reservation`) de los Módulos `01-auth`, `02-cocheras` y `03-reservas`, además del servicio `issue_entry_qr` del Módulo `04-qr-acceso`.
  - Confirmar presencia/autorización de `djangorestframework` en `requirements.txt` (si no está instalado, solicitar aprobación al usuario antes de modificar `requirements.txt`).

---

## Fase 1: Modelos `Pago` y `RetiroCobro` y Migraciones (Rojo -> Verde)

- [ ] **TASK-05-01 [ROJO]: Escribir tests de modelos para `Pago` y `RetiroCobro`**
  - Crear `ParkShare_Backend/tests/test_payment_models.py` con pruebas que deben **fallar inicialmente**:
    1. Creación de `Pago` vinculado a `Reserva` con `monto_total` (`DECIMAL(8, 2)`), `metodo='TARJETA'`, `referencia_pasarela` nullable y default `estado='PENDIENTE'`.
    2. Rechazo de `estado` fuera de `('PENDIENTE', 'APROBADO', 'RECHAZADO', 'REEMBOLSADO')` y rechazo de `monto_total < 0` en validación/constraints.
    3. Rechazo de segundo `Pago` en estado `'APROBADO'` para la misma `reserva` (integridad de unicidad / servicio).
    4. Creación válida de `RetiroCobro` vinculado a `Usuario` (`arrendador`) con `cuenta_cobro`, `monto > 0`, `referencia` única y default `estado='COMPLETADO'`.
    5. Rechazo de `referencia` duplicada en `RetiroCobro` (`IntegrityError`) y rechazo de `monto <= 0`.
  - Ejecutar `python manage.py test` y verificar el fallo esperado (Fase Roja).

- [ ] **TASK-05-02 [VERDE]: Implementar modelos `Pago` y `RetiroCobro` y migrar BD**
  - Implementar `Pago` (`Meta.db_table = 'pago'`) y `RetiroCobro` (`Meta.db_table = 'retiro_cobro'`) en `ParkShare_Backend/models/`.
  - Configurar índices sobre `(reserva, estado)`, `referencia_pasarela`, `(arrendador, estado, fecha_retiro)` y `CheckConstraint` de montos no negativos / positivos.
  - Ejecutar `python manage.py makemigrations` y `python manage.py migrate`.
  - Ejecutar `python manage.py test` sobre `test_payment_models` y verificar el 100% en Verde.

---

## Fase 2: Puente de Pasarela (`puentes/`), Servicios Financieros y Reglas `RN`

- [ ] **TASK-05-03 [ROJO]: Escribir tests de adaptador de pasarela y lógica de negocio (`RN-02`..`RN-05`)**
  - Crear `ParkShare_Backend/tests/test_payment_services.py` probando:
    1. `SimulatedPaymentGatewayAdapter`: retorna aprobación con referencia única ante token válido y rechazo ante `tok_rechazado`.
    2. `process_reservation_payment(reserva, renter, metodo, token_pago)`:
       - Pago aprobado: crea `Pago(APROBADO, monto_total=monto_base)`, cambia reserva `SOLICITADA -> CONFIRMADA` y llama al servicio de Módulo 04 para emitir `CodigoQR(ENTRADA)`.
       - Pago rechazado: crea `Pago(RECHAZADO)`, cambia reserva `SOLICITADA -> CANCELADA`, y **no** emite `CodigoQR`.
       - Rechazo de intento sobre reserva no `SOLICITADA` o ya pagada.
    3. `apply_late_checkout_charge(reserva)`: actualiza `pago.monto_total = reserva.monto_base + reserva.monto_penalizacion` tras `RN-03` ($10 MXN/min después de los 10 min de `RN-02`).
    4. `handle_reservation_cancellation(reserva, now)`:
       - Cancelación `<= 10 min` (`RN-04`): `monto_penalizacion = Decimal('0.00')` y `Pago(APROBADO)` pasa a `'REEMBOLSADO'`.
       - Cancelación `> 10 min` (`RN-05`): asigna `monto_penalizacion = Decimal('100.00')` y refleja la multa en el registro financiero.
    5. `get_host_financial_summary(host_user, now)` y `withdraw_host_balance(host_user, cuenta_cobro)`:
       - Cálculo exacto de `ganado_hoy` (en zona `America/Mexico_City`), `disponible_para_retirar` (solo reservas `FINALIZADA` menos retiros previos) y clasificación de movimientos `Pagado` vs `Pendiente de uso`.
       - Retiro descuenta el saldo disponible a `"0.00"` y rechaza retiros con saldo `<= 0.00` o sin `cuenta_cobro`.
  - Ejecutar `python manage.py test` y verificar fallo inicial (Fase Roja).

- [ ] **TASK-05-04 [VERDE + REFACTOR]: Implementar Bridge de pasarela y servicios de pago/liquidación**
  - Implementar interfaz `PaymentGatewayBridge` y `SimulatedPaymentGatewayAdapter` en `ParkShare_Backend/puentes/payment_gateway.py`.
  - Implementar servicios en `ParkShare_Backend/services/payment_service.py` usando `transaction.atomic()`, `select_for_update()`, cálculos estrictos con `decimal.Decimal` y `select_related('reserva__cochera')` para prevenir N+1.
  - Conectar el hook de ajuste de recargo con el servicio de salida del Módulo 04 (`04-qr-acceso`).
  - Ejecutar `python manage.py test` en Verde y refactorizar helpers de enmascaramiento de `cuenta_cobro` y agregación ORM (`Sum` / `Coalesce`).

---

## Fase 3: Serializadores y Endpoints REST (`/pagos`, `/arrendador/cobros`, `/arrendador/cobros/retirar`)

- [ ] **TASK-05-05 [ROJO]: Escribir tests de integración API (`APITestCase`)**
  - Crear `ParkShare_Backend/tests/test_payment_api.py` cubriendo:
    1. `POST /pagos`:
       - `201 Created` cuando la pasarela aprueba (confirma reserva y crea QR de entrada).
       - `402 Payment Required` cuando la pasarela rechaza (cancela reserva sin crear QR).
       - `400 Bad Request` por campos faltantes o método inválido.
       - `401 Unauthorized` sin token, `403 Forbidden` por usuario no dueño o rol `ARRENDADOR`, `404 Not Found` por reserva inexistente.
       - `409 Conflict` al intentar pagar dos veces o pagar una reserva cancelada/confirmada.
    2. `GET /arrendador/cobros`:
       - `200 OK` devolviendo `ganado_hoy`, `disponible_para_retirar`, `cuenta_cobro` enmascarada y `movimientos` (`Pagado` / `Pendiente de uso`), incluyendo filtro opcional `?estado=`.
       - `403 Forbidden` si el usuario es `ARRENDATARIO` o `ARRENDADOR` pendiente (`RN-08`).
    3. `POST /arrendador/cobros/retirar`:
       - `201 Created` registrando retiro y dejando `disponible_para_retirar: "0.00"`.
       - `400 Bad Request` si falta `cuenta_cobro` o formato inválido.
       - `409 Conflict` si `disponible_para_retirar == "0.00"` (incluyendo cuando solo existen reservas `Pendiente de uso`).
       - Prueba de concurrencia lógica verificando protección contra doble retiro.
  - Ejecutar `python manage.py test` y verificar fallo inicial (Fase Roja).

- [ ] **TASK-05-06 [VERDE + REFACTOR]: Implementar serializadores, vistas y rutas URL**
  - Crear serializadores DRF (`ProcessPaymentSerializer`, `PagoResponseSerializer`, `HostFinancialSummarySerializer`, `WithdrawBalanceSerializer`) en `ParkShare_Backend/serializers/payment_serializers.py`.
  - Implementar vistas (`ProcessPaymentView`, `HostEarningsView`, `HostWithdrawView`) en `ParkShare_Backend/views/payment_views.py` con permisos por rol (`ARRENDATARIO` / `ARRENDADOR` verificado) y mensajes en español.
  - Registrar rutas `pagos`, `arrendador/cobros` y `arrendador/cobros/retirar` en `ParkShare_Backend/urls.py`.
  - Ejecutar tests de API en Verde y refactorizar asegurando respuestas homogéneas de error.

---

## Fase 4: Verificación Final y Auditoría por el `Reviewer`

- [ ] **TASK-05-07 [REVISIÓN]: Auditoría integral y suite completa de pruebas**
  - Ejecutar `python manage.py check` y `python manage.py makemigrations --check --dry-run`.
  - Ejecutar `python manage.py test` completo y certificar **0 errores y 0 fallos**.
  - Verificar checklist de auditoría para el **Reviewer**:
    - [ ] Tabla `pago` coincide 100% con el Diccionario de Datos (`DECIMAL(8,2)`, enums exactos, nulidad).
    - [ ] Integración limpia mediante `ParkShare_Backend/puentes/` sin credenciales ni datos de tarjeta almacenados.
    - [ ] Cobertura completa de `CU-06`, `CU-07`, `RN-02`, `RN-03`, `RN-04` y `RN-05`.
    - [ ] Protección transaccional (`select_for_update`) contra pagos duplicados y retiros dobles.
    - [ ] Cálculos con `Decimal` y zona horaria `America/Mexico_City`.
