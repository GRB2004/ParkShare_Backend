# Especificación Técnica: Módulo 05 — Pagos, Cobros y Liquidaciones

> **Módulo:** `05-pagos-cobros`  
> **Estado:** Listo para Implementación (TDD)  
> **Versión:** 1.0.0  
> **Fecha:** 2026-11-19  
> **Proyecto:** `ParkShare_Backend` (Django + Django REST Framework + MySQL)  
> **Trazabilidad MDE:** CIM (`CU-07`), PIM/PSM (`Pago`, `Reserva`), Reglas de Negocio (`RN-02`, `RN-03`, `RN-04`, `RN-05`)

---

## 1. Resumen y Alcance

El **Módulo 05 (`05-pagos-cobros`)** gestiona el procesamiento de pagos de reservas por parte del **Arrendatario**, la aplicación financiera de penalizaciones y reembolsos, la integración desacoplada con la pasarela de pagos mediante patrón Puente/Adaptador (`ParkShare_Backend/puentes/`), y el panel financiero y retiro de ingresos del **Arrendador**.

### Alcance dentro de `ParkShare_Backend`
- Registro y procesamiento de cobro de reservas (`POST /pagos`) utilizando el adaptador de pasarela de pagos (`PaymentGatewayBridge`).
- Transición automática de la reserva tras la respuesta de la pasarela:
  - Pago `APROBADO` -> reserva `SOLICITADA` pasa a `CONFIRMADA` y se dispara la emisión del QR de `ENTRADA` (`CU-06`, Módulo 04).
  - Pago `RECHAZADO` -> reserva `SOLICITADA` pasa a `CANCELADA`.
- Soporte financiero para penalizaciones por cancelación tardía (`RN-05`: `$100.00 MXN`), reembolso por cancelación sin costo (`RN-04`), y recargo por retraso en salida (`RN-02` y `RN-03`: `$10.00 MXN/min` tras 10 minutos de tolerancia).
- Consulta del panel financiero del arrendador (`GET /arrendador/cobros`): `ganado_hoy`, `disponible_para_retirar`, `cuenta_cobro` y lista de `movimientos` (`Pagado` o `Pendiente de uso`).
- Ejecución atómica de retiros del saldo disponible hacia la `cuenta_cobro` del arrendador (`POST /arrendador/cobros/retirar`).

### Fuera de alcance
- Almacenamiento de datos sensibles de tarjetas (PAN completo o CVV; el backend solo opera con método y token/referencia de pasarela cumpliendo buenas prácticas de seguridad).
- Validación física de códigos QR (cubierta en `specs/04-qr-acceso/`).

---

## 2. Casos de Uso Cubiertos (CIM)

| ID | Caso de Uso | Actor Principal | Descripción del Flujo en Backend |
| :--- | :--- | :--- | :--- |
| **CU-07 (Flujo Pago)** | **Pagar reserva** | Arrendatario | El arrendatario inicia el pago de su reserva en estado `SOLICITADA`. El backend calcula `monto_total = monto_base + monto_penalizacion`, crea un registro `Pago` en estado `PENDIENTE`, invoca el puente de pasarela y actualiza atómicamente a `APROBADO` (confirmando la reserva y emitiendo QR de `ENTRADA`) o `RECHAZADO` (cancelando la reserva). |
| **CU-07 (Flujo Cobro)** | **Consultar ingresos y retirar saldo** | Arrendador | El arrendador consulta su resumen financiero diario, saldo liberado disponible para retiro, cuenta de cobro vinculada y desglose de movimientos (`Pagado` para reservas `FINALIZADA`s, `Pendiente de uso` para reservas `CONFIRMADA`/`EN_USO`), y puede solicitar el retiro total del saldo disponible. |

---

## 3. Reglas de Negocio Aplicables (`RN-XX`)

| Regla | Aplicación Específica en el Módulo 05 |
| :--- | :--- |
| **Aprobación / Rechazo de Pago (`CU-07`, `CU-06`)** | El pago exitoso (`APROBADO`) del `monto_base` aprueba la reserva (`SOLICITADA -> CONFIRMADA`, invocando `confirm_reservation` de `03-reservas`) y dispara automáticamente la emisión del `CodigoQR` con `tipo_qr = 'ENTRADA'`. Si el pago es `RECHAZADO`, la reserva pasa de `SOLICITADA` a `CANCELADA` y no se emite QR. |
| **RN-02 (Tiempo de tolerancia)** | Los primeros 10 minutos posteriores a `reserva.fecha_hora_fin` no generan recargo (`monto_penalizacion = 0.00`), por lo que el `pago.monto_total` permanece igual al `monto_base`. |
| **RN-03 (Recargo por retraso)** | Cuando el checkout (`Módulo 04`) registra una salida posterior a `fecha_hora_fin + 10 min`, calcula `$10.00 MXN` por cada minuto de retraso en `reserva.monto_penalizacion`. El servicio financiero del Módulo 05 procesa el ajuste correspondiente reflejando el total cobrado en `pago.monto_total = reserva.monto_base + reserva.monto_penalizacion` (y acreditándolo en el movimiento del arrendador como `Pagado`). |
| **RN-04 (Cancelación sin costo)** | Si el arrendatario cancela dentro de los primeros **10 minutos** posteriores a haber realizado la reserva (`fecha_creacion + 10 min`), `monto_penalizacion = 0.00`. Si existía un `Pago` `APROBADO`, el servicio financiero solicita reembolso a la pasarela y actualiza `pago.estado = 'REEMBOLSADO'`. Este movimiento no suma al saldo disponible del arrendador. |
| **RN-05 (Multa por cancelación tardía)** | Si el arrendatario cancela después de transcurridos los 10 minutos desde la creación de la reserva, se asigna `reserva.monto_penalizacion = Decimal('100.00')`.<br>- Si la reserva tenía un `Pago` `APROBADO`, se registra el cobro/retención de la multa de `$100.00 MXN` en la liquidación (reembolso parcial de `monto_base - 100.00` si `monto_base > 100.00`, o retención del monto cubierto hasta `$100.00`, reflejando el cargo de multa en el registro financiero).<br>- Si la reserva estaba `SOLICITADA` (sin pago previo) y se liquida la multa pendiente, `monto_total` cobrado por penalización es `Decimal('100.00')`. |

### Arquitectura de Pasarela (Patrón Bridge en `ParkShare_Backend/puentes/`)

- Se define una interfaz abstracta `PaymentGatewayBridge` en `ParkShare_Backend/puentes/payment_gateway.py` con las operaciones:
  - `charge(amount, method, token_pago, metadata) -> GatewayResult(approved: bool, reference: str | None, message: str)`
  - `refund(reference, amount) -> GatewayResult`
  - `payout(destination_account, amount, metadata) -> GatewayResult`
- Implementación por defecto para entorno de desarrollo y pruebas: `SimulatedPaymentGatewayAdapter` (configurable vía `settings.PAYMENT_GATEWAY_ADAPTER`), que permite simular aprobación determinista, rechazo controlado (ej. `token_pago == "tok_rechazado"` o método inválido) y generación de referencias únicas tipo `"PS-PAY-<uuid>"`.

---

## 4. Modelo de Datos (Diccionario de Datos / PSM)

### 4.1 Tabla `pago` (Diccionario de Datos Oficial)

Modelo Django sugerido: `Pago` (`Meta.db_table = 'pago'`).

| Columna | Tipo SQL (MySQL) | Tipo Django ORM | Nulidad | Default / Restricciones | Descripción |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `id_pago` | `BIGINT AUTO_INCREMENT` | `models.BigAutoField(primary_key=True)` | No nulo | `PK` | Identificador único del pago. |
| `reserva_id` | `BIGINT` | `models.ForeignKey('Reserva', db_column='reserva_id', on_delete=models.PROTECT, related_name='pagos')` | No nulo | `FK -> reserva.id_reserva`, `INDEX` | Reserva asociada al pago. |
| `monto_total` | `DECIMAL(8, 2)` | `models.DecimalField(max_digits=8, decimal_places=2)` | No nulo | `CHECK (monto_total >= 0)` | Importe total del pago (`monto_base + monto_penalizacion`). |
| `metodo` | `VARCHAR(50)` | `models.CharField(max_length=50)` | No nulo | Ej. `'TARJETA'` | Método de pago utilizado. |
| `referencia_pasarela` | `VARCHAR(100)` | `models.CharField(max_length=100, null=True, blank=True)` | Sí nulo | `INDEX` | Identificador de transacción devuelto por la pasarela externa/simulada. |
| `estado` | `ENUM('PENDIENTE', 'APROBADO', 'RECHAZADO', 'REEMBOLSADO')` | `models.CharField(max_length=15, choices=EstadoPago.choices, default='PENDIENTE')` | No nulo | Default `'PENDIENTE'`, `INDEX` | Estado actual de la transacción de pago. |
| `fecha_pago` | `DATETIME` | `models.DateTimeField()` | No nulo | `timezone.now` | Fecha y hora de registro/procesamiento del pago. |

#### Reglas de Integridad sobre `pago`
- No puede existir más de un `Pago` en estado `'APROBADO'` para la misma `reserva_id` (validado mediante `UniqueConstraint(fields=['reserva'], condition=Q(estado='APROBADO'), name='uq_pago_aprobado_reserva')` en capa de servicio/BD; en MySQL sin índices parciales nativos, la regla se asegura además dentro de `transaction.atomic()` con `select_for_update()` sobre la `Reserva` verificando que no exista un pago `APROBADO` previo).
- Un pago en estado `'RECHAZADO'` no impide consultar el historial, pero si la reserva pasa a `'CANCELADA'` no admite reintentos de pago sobre esa reserva cancelada.

### 4.2 Persistencia Auxiliar para Retiros y Uso de `usuario.cuenta_cobro`

> **Nota arquitectónica:** Para cumplir el contrato REST de `GET /arrendador/cobros` y `POST /arrendador/cobros/retirar` (*"Registra el retiro del saldo disponible hacia la `cuenta_cobro` del arrendador"*), se utiliza la columna `usuario.cuenta_cobro` (`VARCHAR(100)`, definida en `01-auth`) y se registra cada operación de egreso en la tabla auxiliar `retiro_cobro`, manteniendo intacta la definición normativa de la tabla `pago`.

#### Campo `usuario.cuenta_cobro` del Arrendador (`01-auth`)
- Se lee directamente de `usuario.cuenta_cobro` (`VARCHAR(100)`, nullable en el Diccionario de Datos de `01-auth`, ej. CLABE de 18 dígitos). Si el arrendador aún no la tiene registrada en su perfil y la envía en `POST /arrendador/cobros/retirar`, el backend la valida (16–18 dígitos numéricos), la guarda en `usuario.cuenta_cobro` y ejecuta el retiro.

#### Tabla `retiro_cobro`
Modelo Django sugerido: `RetiroCobro` (`Meta.db_table = 'retiro_cobro'`).

| Columna | Tipo SQL (MySQL) | Tipo Django ORM | Nulidad | Default / Restricciones | Descripción |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `id_retiro` | `BIGINT AUTO_INCREMENT` | `models.BigAutoField(primary_key=True)` | No nulo | `PK` | Identificador único del retiro. |
| `arrendador_id` | `BIGINT` | `models.ForeignKey('Usuario', db_column='arrendador_id', on_delete=models.PROTECT, related_name='retiros')` | No nulo | `FK -> usuario.id_usuario`, `INDEX` | Arrendador beneficiario del retiro. |
| `cuenta_cobro` | `VARCHAR(100)` | `models.CharField(max_length=100)` | No nulo | Validación no vacío | Cuenta o CLABE destino donde se depositó el retiro. |
| `monto` | `DECIMAL(10, 2)` | `models.DecimalField(max_digits=10, decimal_places=2)` | No nulo | `CHECK (monto > 0)` | Monto retirado en MXN. |
| `referencia` | `VARCHAR(100)` | `models.CharField(max_length=100, unique=True)` | No nulo | `UNIQUE` | Folio o referencia de transferencia del adaptador. |
| `estado` | `ENUM('PENDIENTE', 'COMPLETADO', 'FALLIDO')` | `models.CharField(max_length=15, default='COMPLETADO')` | No nulo | Default `'COMPLETADO'` | Estado del retiro. |
| `fecha_retiro` | `DATETIME` | `models.DateTimeField()` | No nulo | `timezone.now` | Fecha y hora de ejecución del retiro. |

### 4.3 Reglas de Cálculo Financiero del Arrendador
Para un `arrendador` dado (en zona horaria `America/Mexico_City`):
1. **Movimientos mostrados en `GET /arrendador/cobros`**:
   - Incluyen reservas de cocheras del arrendador que cuenten con un `Pago` en estado `'APROBADO'`:
     - Si `reserva.estado == 'FINALIZADA'` -> estado visual del movimiento: **`"Pagado"`** (ingreso ya liberado tras finalizar el servicio; monto = `pago.monto_total`).
     - Si `reserva.estado in ('CONFIRMADA', 'EN_USO')` -> estado visual del movimiento: **`"Pendiente de uso"`** (el arrendatario ya pagó, pero el servicio aún no finaliza, por lo que no es retirable todavía).
     - *(Nota: reservas con pago `RECHAZADO` o `REEMBOLSADO` por `RN-04` no suman saldo ni aparecen como cobros pendientes de uso).*
     - *(Si hubo cancelación tardía `RN-05` con multa retenida de `$100.00`, en caso de corresponder abono al arrendador por bloqueo de espacio o retención según política, solo los movimientos `Pagado` liberados se contabilizan en `disponible_para_retirar` — por defecto en el MVP, los ingresos retirables provienen de reservas `FINALIZADA`s con pago `APROBADO`).*
2. **`ganado_hoy`**:
   - Suma de `pago.monto_total` de movimientos con estado `"Pagado"` (`reserva.estado == 'FINALIZADA'` y `pago.estado == 'APROBADO'`) cuya fecha de finalización (`COALESCE(reserva.hora_salida_real, reserva.fecha_hora_fin)` convertida a `America/Mexico_City`) corresponda al día calendario actual (`hoy`).
3. **`disponible_para_retirar`**:
   - `(Suma total de pago.monto_total de movimientos en estado "Pagado") - (Suma total de retiro_cobro.monto con estado == 'COMPLETADO' del arrendador)`. Nunca puede ser menor a `0.00`.

---

## 5. Contratos de API REST (Endpoints)

### 5.1 `POST /pagos`

Procesa el cobro de una reserva a través del adaptador de pasarela de pagos.

- **Autenticación:** Requerida.
- **Permisos / Rol:** Usuario autenticado con rol `ARRENDATARIO` y titular de la reserva (`reserva.arrendatario_id == request.user.id_usuario`).
- **Cuerpo de petición (Request Payload JSON):**

```json
{
  "reserva_id": 105,
  "metodo": "TARJETA",
  "token_pago": "tok_visa_4242"
}
```

#### Validaciones de Entrada
- `reserva_id`: Entero obligatorio existente.
- `metodo`: Cadena obligatoria no vacía, máx. 50 chars. Valores permitidos: `'TARJETA'` (extensible a `'TARJETA_DEBITO'`, `'TARJETA_CREDITO'`).
- `token_pago`: Cadena opcional/obligatoria para pasarela (si no se envía, en simulador se asume token válido por defecto o según configuración; si se envía `"tok_rechazado"`, la pasarela simulada retorna rechazo).

#### Respuestas Esperadas

- **`201 Created`** — Pago **APROBADO**. Crea `Pago(estado='APROBADO')`, actualiza reserva `SOLICITADA -> CONFIRMADA` y genera `CodigoQR(tipo_qr='ENTRADA')`:

```json
{
  "id_pago": 88,
  "reserva_id": 105,
  "monto_total": "80.00",
  "metodo": "TARJETA",
  "referencia_pasarela": "PS-PAY-7f9a1c3b2d4e",
  "estado": "APROBADO",
  "fecha_pago": "2026-11-19T14:05:00-06:00",
  "estado_reserva": "CONFIRMADA",
  "qr_entrada_generado": true,
  "mensaje": "Pago aprobado. Tu reserva está confirmada y tu código QR de entrada está listo."
}
```

- **`402 Payment Required`** — Pago **RECHAZADO** por la pasarela. Crea `Pago(estado='RECHAZADO')` y transiciona la reserva `SOLICITADA -> CANCELADA`:

```json
{
  "id_pago": 89,
  "reserva_id": 105,
  "monto_total": "80.00",
  "metodo": "TARJETA",
  "referencia_pasarela": "PS-DECL-0019",
  "estado": "RECHAZADO",
  "fecha_pago": "2026-11-19T14:05:00-06:00",
  "estado_reserva": "CANCELADA",
  "qr_entrada_generado": false,
  "detail": "El pago fue rechazado por la pasarela. La reserva ha sido cancelada."
}
```

- **`400 Bad Request`** — Campos faltantes o método no válido.
- **`401 Unauthorized`** — No autenticado.
- **`403 Forbidden`** — El usuario no es `ARRENDATARIO` o la reserva pertenece a otro usuario.
- **`404 Not Found`** — La reserva `reserva_id` no existe.
- **`409 Conflict`** — La reserva no está en estado `'SOLICITADA'` (ej. ya está `CONFIRMADA`, `CANCELADA` o `FINALIZADA`) o ya cuenta con un pago `APROBADO`.

---

### 5.2 `GET /arrendador/cobros`

Devuelve el panel financiero completo del arrendador autenticado.

- **Autenticación:** Requerida.
- **Permisos / Rol:** Usuario autenticado con rol `ARRENDADOR` (cuenta activa y verificada según `RN-08`).
- **Parámetros de consulta opcionales (Query Params):**
  - `estado`: `'Pagado'` | `'Pendiente de uso'` (filtra la lista de `movimientos` sin alterar los totales globales `ganado_hoy` y `disponible_para_retirar`).
- **Cuerpo de petición:** No aplica.

#### Respuestas Esperadas

- **`200 OK`**:

```json
{
  "ganado_hoy": "180.00",
  "disponible_para_retirar": "420.00",
  "cuenta_cobro": "****4321",
  "movimientos": [
    {
      "id_pago": 88,
      "reserva_id": 105,
      "cochera": "Cochera Centro Norte",
      "fecha_horario": "2026-11-19 16:00 - 18:00",
      "monto": "180.00",
      "monto_base": "80.00",
      "monto_penalizacion": "100.00",
      "estado": "Pagado",
      "fecha_pago": "2026-11-19T14:05:00-06:00"
    },
    {
      "id_pago": 91,
      "reserva_id": 108,
      "cochera": "Cochera Centro Norte",
      "fecha_horario": "2026-11-20 10:00 - 12:00",
      "monto": "70.00",
      "monto_base": "70.00",
      "monto_penalizacion": "0.00",
      "estado": "Pendiente de uso",
      "fecha_pago": "2026-11-19T15:00:00-06:00"
    }
  ]
}
```

- **`401 Unauthorized`** — No autenticado.
- **`403 Forbidden`** — Usuario no es `ARRENDADOR` o cuenta pendiente/suspendida (`RN-08`).

---

### 5.3 `POST /arrendador/cobros/retirar`

Registra el retiro del saldo completo disponible hacia la `cuenta_cobro` del arrendador autenticado.

- **Autenticación:** Requerida.
- **Permisos / Rol:** Usuario autenticado con rol `ARRENDADOR` (cuenta activa y verificada según `RN-08`).
- **Cuerpo de petición (Request Payload JSON):**
  - Opcionalmente vacío `{}` si el arrendador ya tiene registrada `cuenta_cobro` en su perfil; o bien puede indicar `cuenta_cobro` (16 o 18 dígitos numéricos, ej. CLABE) si aún no está guardada o desea usar/registrar esa cuenta:

```json
{
  "cuenta_cobro": "012180001234567890"
}
```

#### Respuestas Esperadas

- **`201 Created`** — Retiro procesado exitosamente por el total de `disponible_para_retirar`:

```json
{
  "id_retiro": 12,
  "monto_retirado": "420.00",
  "cuenta_cobro": "****7890",
  "referencia": "PS-RET-a81c92e401",
  "estado": "COMPLETADO",
  "fecha_retiro": "2026-11-19T19:00:00-06:00",
  "disponible_para_retirar": "0.00",
  "mensaje": "Retiro procesado correctamente hacia tu cuenta de cobro."
}
```

- **`400 Bad Request`** — No hay `cuenta_cobro` configurada en el perfil ni enviada en el payload, o el formato de `cuenta_cobro` es inválido (no corresponde a 16-18 dígitos):

```json
{
  "error": "MissingPayoutAccount",
  "detail": "Debes registrar una cuenta de cobro válida antes de solicitar un retiro."
}
```

- **`401 Unauthorized`** — No autenticado.
- **`403 Forbidden`** — Usuario no es `ARRENDADOR` o cuenta no verificada (`RN-08`).
- **`409 Conflict`** — `disponible_para_retirar <= 0.00` (solo existen cobros `Pendiente de uso` o el saldo ya fue retirado):

```json
{
  "error": "NoFundsAvailable",
  "detail": "No tienes saldo disponible para retirar en este momento."
}
```

---

## 6. Escenarios de Prueba para TDD

### 6.1 Casos Felices (Happy Path)
1. **Pago aprobado (`POST /pagos`, `CU-07`)**: Reserva en `SOLICITADA` con `monto_base = 80.00` y método `"TARJETA"` -> crea `Pago(estado='APROBADO', monto_total=80.00)` con `referencia_pasarela`, la reserva transiciona a `CONFIRMADA`, y existe un `CodigoQR(tipo_qr='ENTRADA', usado=False)` creado automáticamente.
2. **Pago rechazado (`POST /pagos`)**: Pasarela simulada retorna rechazo (`token_pago='tok_rechazado'`) -> retorna `402 Payment Required`, crea `Pago(estado='RECHAZADO')`, reserva pasa a `CANCELADA`, y **NO** se genera ningún `CodigoQR`.
3. **Ajuste de cobro por retraso en salida (`RN-02`, `RN-03`)**: Reserva con `monto_base=80.00` y `Pago(APROBADO)` finaliza con 10 min de retraso fuera de tolerancia (`monto_penalizacion=100.00`) -> `pago.monto_total` refleja `180.00` y aparece como `"Pagado"` por `"180.00"` en `GET /arrendador/cobros`.
4. **Reembolso por cancelación gratuita (`RN-04`)**: Reserva pagada cancelada dentro de los primeros 10 min -> `pago.estado` pasa a `'REEMBOLSADO'`, `monto_penalizacion=0.00`, y no suma al `disponible_para_retirar` del arrendador.
5. **Multa por cancelación tardía (`RN-05`)**: Reserva cancelada >10 min después de su creación -> `reserva.monto_penalizacion == Decimal('100.00')` y el servicio financiero registra/refleja el cargo de `$100.00 MXN`.
6. **Panel financiero del arrendador (`GET /arrendador/cobros`)**:
   - Arrendador con 1 reserva `FINALIZADA` hoy (`180.00`), 1 reserva `FINALIZADA` ayer (`240.00`), 1 reserva `CONFIRMADA` (`70.00`) y 1 `REEMBOLSADO` (`50.00`):
   - `ganado_hoy == "180.00"`, `disponible_para_retirar == "420.00"`, lista contiene movimientos `Pagado` y `Pendiente de uso` con cochera, fecha/horario y monto correctos.
7. **Retiro exitoso (`POST /arrendador/cobros/retirar`)**: Con `disponible_para_retirar == "420.00"` y `cuenta_cobro` válida -> retorna `201 Created`, crea `RetiroCobro(monto=420.00, estado='COMPLETADO')` y al volver a llamar `GET /arrendador/cobros`, `disponible_para_retirar == "0.00"` mientras `ganado_hoy` conserva el histórico del día.

### 6.2 Casos de Error y Validación
1. **Acceso sin autenticación**: Los 3 endpoints responden `401 Unauthorized`.
2. **Autorización por rol/propiedad**:
   - Arrendador intentando llamar `POST /pagos` o arrendatario intentando pagar reserva ajena -> `403 Forbidden`.
   - Arrendatario intentando acceder a `GET /arrendador/cobros` o `POST /arrendador/cobros/retirar` -> `403 Forbidden`.
   - Arrendador con cuenta pendiente/inactiva (`RN-08`) -> `403 Forbidden`.
3. **Reserva inexistente en `POST /pagos`**: `reserva_id` no existe -> `404 Not Found`.
4. **Método inválido o payload incompleto en `POST /pagos`**: `metodo` vacío o desconocido -> `400 Bad Request`.
5. **Pago duplicado o sobre estado inválido**: Intentar pagar una reserva ya `CONFIRMADA`, `EN_USO`, `FINALIZADA` o `CANCELADA` -> `409 Conflict` sin invocar la pasarela ni duplicar pagos.
6. **Retiro sin cuenta de cobro**: `POST /arrendador/cobros/retirar` sin `cuenta_cobro` registrada en perfil ni enviada en el payload -> `400 Bad Request`.
7. **Retiro sin fondos disponibles**: `POST /arrendador/cobros/retirar` cuando solo hay reservas `Pendiente de uso` o saldo es `0.00` -> `409 Conflict`.

### 6.3 Casos Límite (Edge Cases)
1. **Concurrencia en doble retiro simultáneo**: Dos peticiones concurrentes a `POST /arrendador/cobros/retirar` del mismo arrendador (`select_for_update()` sobre el registro del usuario) -> exactamente una retira el saldo (`201 Created`) y la segunda recibe `409 Conflict` sin generar doble egreso.
2. **Concurrencia en doble pago simultáneo**: Dos peticiones simultáneas a `POST /pagos` para la misma `reserva_id` -> solo la primera procesa el cobro y confirma la reserva; la segunda recibe `409 Conflict`.
3. **Enmascaramiento de `cuenta_cobro`**: En las respuestas de `GET /arrendador/cobros` y `POST /arrendador/cobros/retirar`, las cuentas bancarias/CLABE se devuelven enmascaradas (ej. últimos 4 dígitos `****7890`) sin exponer la cuenta completa innecesariamente.
4. **Filtro por zona horaria `America/Mexico_City` en `ganado_hoy`**: Una reserva finalizada a las `23:30` hora local Ciudad de México (que ya es día siguiente en UTC) debe seguir contabilizándose en `ganado_hoy` del día local actual.
5. **Integridad de decimales**: Cálculos monetarios ejecutados estrictamente con `decimal.Decimal` (`ROUND_HALF_UP`) sin pérdida de precisión en centavos.
