# Especificación Técnica: Módulo 04 — Generación y Validación de Códigos QR

> **Módulo:** `04-qr-acceso`  
> **Estado:** Listo para Implementación (TDD)  
> **Versión:** 1.1.0  
> **Fecha:** 2026-11-24  
> **Proyecto:** `ParkShare_Backend` (Django + Django REST Framework + MySQL)  
> **Trazabilidad MDE:** CIM (`CU-06`, `CU-08`), PIM/PSM (`CodigoQR`, `Reserva`), Reglas de Negocio (`RN-02`, `RN-03`, `RN-06`)

---

## 1. Resumen y Alcance

El **Módulo 04 (`04-qr-acceso`)** implementa el subsistema de control de acceso físico y cierre de estancia basado en códigos QR criptográficos de un solo uso para la plataforma **ParkShare**. Su propósito es garantizar que únicamente el conductor (**Arrendatario**) con una reserva pagada y vigente pueda ingresar a una cochera privada, y que el propietario (**Arrendador**) valide de forma segura tanto la llegada como la salida del vehículo.

### Alcance dentro de `ParkShare_Backend`
- Generación de tokens criptográficos únicos SHA-256 (64 caracteres hexadecimales) asociados a una reserva (`CodigoQR`).
- Emisión automática del QR de `ENTRADA` cuando el pago es aprobado y la reserva transiciona a `CONFIRMADA` (invocado desde Módulo 05) y consulta enriquecida del QR activo por parte del arrendatario (`GET /reservas/{id}/qr`), incluyendo datos de `cochera`, `arrendatario` y `monto_base` requeridos por `QrDisplayCard.jsx` y `ReservationQrScreen.jsx`.
- Validación atómica y transaccional del QR escaneado por el arrendador (`POST /reservas/validar`) tanto para acceso (`ENTRADA`) como para salida (`SALIDA`).
- Soporte de **Simulación de escaneo QR** para el bloque *"Pruebas rápidas de escaneo"* de `QrScannerScreen.jsx` (`GET /reservas/simular-qr` para listar QRs activos escaneables de las cocheras del arrendador, y `POST /reservas/simular-qr` para ejecutar la simulación directa por `token`, `reserva_id` y/o `tipo_qr`).
- Transición segura de estados de la reserva (`CONFIRMADA` -> `EN_USO` -> `FINALIZADA`), registro de `hora_salida_real` y cálculo exacto de recargos por retraso fuera de tolerancia (`RN-02`, `RN-03`).
- Integración mediante capa de servicios/puentes con el Módulo 03 (`Reserva`) y Módulo 05 (`Pago` / ajuste por penalización).

### Fuera de alcance
- Renderizado visual de la imagen QR o escáner con cámara (responsabilidad exclusiva del cliente móvil React Native `ParkShareApp`).
- Procesamiento de cobro inicial o retiros bancarios (cubiertos en `specs/05-pagos-cobros/`).

---

## 2. Casos de Uso Cubiertos (CIM)

| ID | Caso de Uso | Actor Principal | Descripción del Flujo en Backend |
| :--- | :--- | :--- | :--- |
| **CU-06** | **Generar QR de acceso** | Arrendatario (disparado tras pago / consultado por el cliente) | Genera un token criptográfico SHA-256 único para la reserva únicamente cuando el pago ha sido aprobado y la reserva se encuentra en estado `CONFIRMADA`. Permite al arrendatario consultar su QR activo (`ENTRADA` o `SALIDA`) junto con el resumen de cochera (coordenadas para "Cómo llegar"), vehículo y monto base. |
| **CU-08** | **Validar QR de llegada** | Arrendador | El arrendador envía el token escaneado al backend. El servidor valida existencia del token, integridad de estado (`usado == 0`), vigencia temporal, estado de la reserva (`CONFIRMADA`) y propiedad de la cochera (`cochera.arrendador_id == usuario autenticado`). Si es válido, marca el QR de entrada como usado, pasa la reserva a `EN_USO`, emite el QR de `SALIDA` y retorna los datos de verificación del arrendatario y su vehículo. |
| **CU-08 (Extensión Checkout)** | **Validar QR de salida (`RN-06`)** | Arrendador | Al finalizar la estancia, el arrendatario muestra su QR de `SALIDA` y el arrendador lo escanea. El servidor valida token, propiedad y estado `EN_USO`, marca el QR de salida como usado, registra `hora_salida_real = now()`, calcula `monto_penalizacion` según `RN-02` y `RN-03`, y transiciona la reserva a `FINALIZADA`. |
| **CU-08 (Soporte Frontend: Simular Escaneo)** | **Simular escaneo QR** | Arrendador | Permite al arrendador obtener desde el backend sus QRs activos listos para prueba (`GET /reservas/simular-qr`) y disparar la validación de entrada o salida en el flujo de pruebas rápidas de `QrScannerScreen.jsx` (`POST /reservas/simular-qr`), incluso si la reserva confirmada está programada para más tarde en el día. |

---

## 3. Reglas de Negocio Aplicables (`RN-XX`)

| Regla | Aplicación Específica en el Módulo 04 |
| :--- | :--- |
| **RN-06 (QR de salida)** | Al hacer su salida, el arrendatario debe mostrar su código QR con `tipo_qr = 'SALIDA'` para confirmar el checkout. La validación exitosa registra `reserva.hora_salida_real`, marca el QR como `usado = 1` y transiciona la reserva de `EN_USO` a `FINALIZADA`. |
| **RN-02 (Tiempo de tolerancia)** | Al concluir el tiempo reservado (`reserva.fecha_hora_fin`), se otorgan exactamente **10 minutos de tolerancia** (`limite_tolerancia = reserva.fecha_hora_fin + 10 min`) para que el arrendatario retire su vehículo sin cargo adicional (`monto_penalizacion = 0.00`). |
| **RN-03 (Recargo por retraso)** | Si `hora_salida_real > limite_tolerancia`, se cobra **$10.00 MXN por cada minuto de retraso** posterior a los 10 minutos de tolerancia. Ejemplo normativo: salir 10 minutos después del fin de la tolerancia (`fecha_hora_fin + 20 min`) equivale a un cargo de `$100.00 MXN`.<br>**Fórmula determinista:**<br>- Si `hora_salida_real <= fecha_hora_fin + 10 min` -> `minutos_retraso = 0`, `recargo = Decimal("0.00")`.<br>- Si `hora_salida_real > fecha_hora_fin + 10 min` -> `segundos_exceso = (hora_salida_real - (fecha_hora_fin + 10 min)).total_seconds()`; `minutos_retraso = math.ceil(segundos_exceso / 60)`; `recargo = Decimal(minutos_retraso) * Decimal("10.00")`. (El uso de techo por minuto iniciado evita fracciones sin cobro tras vencer la tolerancia y mantiene exactitud en minutos enteros como `600 s = 10 min = $100.00 MXN`). |
| **Alineación con RN-01 / RN-04 / RN-05** | Si una reserva es cancelada (`estado = 'CANCELADA'`) o expirada (`estado = 'EXPIRADA'`), cualquier QR previamente emitido para ella queda rechazado durante la validación (`409 Conflict` / `410 Gone`). Solo reservas `CONFIRMADA` o `EN_USO` pueden operar códigos QR activos. |
| **Tolerancia de llegada (`POST /reservas/validar`)** | En el escaneo real (`POST /reservas/validar`), la llegada abre `15` minutos antes de `fecha_hora_inicio` y tolera hasta `20` minutos después de `fecha_hora_inicio`; si `now > fecha_hora_inicio + 20 min`, la reserva se cancela automáticamente con multa de `$100.00 MXN` (`LateEntryCancelled`, `409 Conflict`). |
| **Regla de Ventana en Simulación (`POST /reservas/simular-qr`)** | En modo simulación rápida (`respetar_ventana = false`, valor por defecto en `POST /reservas/simular-qr`), si la reserva confirmada (`ENTRADA`) está programada para más tarde en el día o en fecha futura (`now < fecha_hora_inicio - 15 min` y `now <= qr.expiracion`), el backend evalúa la entrada tomando como referencia `effective_now = reserva.fecha_hora_inicio`. Con ello se habilita probar entrada y salida inmediata desde `QrScannerScreen.jsx` sin debilitar la validación estricta de `POST /reservas/validar`. Todas las demás reglas (rol `ARRENDADOR` activo `RN-08`, propiedad de cochera, `usado == 0`, no expirado, tolerancia tardía `> inicio + 20 min`, y `RN-02`/`RN-03`/`RN-06` en `SALIDA`) permanecen intactas. Si el cliente envía `"respetar_ventana": true`, se aplica exactamente la ventana anticipada de 15 minutos. |

### Máquina de Estados de Reserva y Códigos QR

```mermaid
stateDiagram-v2
    [*] --> SOLICITADA: Reserva creada
    SOLICITADA --> CONFIRMADA: Pago APROBADO (emite QR ENTRADA)
    SOLICITADA --> CANCELADA: Pago RECHAZADO o cancelación
    CONFIRMADA --> CANCELADA: Cancelación (RN-04 / RN-05)
    CONFIRMADA --> EXPIRADA: No llegó antes de fecha_hora_fin
    CONFIRMADA --> EN_USO: POST /reservas/validar (QR ENTRADA válido -> marca usado=1, emite QR SALIDA)
    EN_USO --> FINALIZADA: POST /reservas/validar (QR SALIDA válido -> marca usado=1, registra hora_salida_real y RN-02/RN-03)
```

---

## 4. Modelo de Datos (Diccionario de Datos / PSM)

### 4.1 Tabla `codigo_qr`

Modelo Django sugerido: `CodigoQR` (`Meta.db_table = 'codigo_qr'`).

| Columna | Tipo SQL (MySQL) | Tipo Django ORM | Nulidad | Default / Restricciones | Descripción |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `id_qr` | `BIGINT AUTO_INCREMENT` | `models.BigAutoField(primary_key=True)` | No nulo | `PK` | Identificador único del código QR. |
| `reserva_id` | `BIGINT` | `models.ForeignKey('Reserva', db_column='reserva_id', on_delete=models.CASCADE, related_name='codigos_qr')` | No nulo | `FK -> reserva.id_reserva`, `INDEX` | Reserva a la que pertenece el código QR. |
| `token_qr` | `VARCHAR(64)` | `models.CharField(max_length=64, unique=True)` | No nulo | `UNIQUE`, formato `[0-9a-f]{64}` | Token criptográfico SHA-256 de 64 caracteres hexadecimales generado con entropía CSPRNG (`secrets.token_bytes(32)` + `hashlib.sha256`). |
| `tipo_qr` | `ENUM('ENTRADA', 'SALIDA')` | `models.CharField(max_length=10, choices=TipoQR.choices)` | No nulo | `CHECK (tipo_qr IN ('ENTRADA', 'SALIDA'))` | Fase del ciclo de uso del código QR. |
| `usado` | `TINYINT(1)` / `BOOLEAN` | `models.BooleanField(default=False)` | No nulo | Default `0` (`False`) | `0` (`False`) = Activo, `1` (`True`) = Usado. |
| `fecha_emision` | `DATETIME` | `models.DateTimeField()` | No nulo | `timezone.now` al emitirse | Fecha y hora UTC/local (aware) de generación del token. |
| `expiracion` | `DATETIME` | `models.DateTimeField()` | No nulo | `INDEX` | Fecha y hora límite de validez del token. |

#### Restricciones e Índices de Integridad (`Meta`)
1. `UNIQUE(token_qr)`: Garantiza unicidad global del token criptográfico.
2. `UNIQUE(reserva_id, tipo_qr)` (`UniqueConstraint(fields=['reserva', 'tipo_qr'], name='uq_codigo_qr_reserva_tipo')`): Garantiza como máximo un QR de `ENTRADA` y un QR de `SALIDA` por reserva, evitando duplicados ante concurrencia.
3. `CheckConstraint`: `token_qr` exactamente de 64 caracteres hexadecimales (validado en `clean()` del modelo y serializadores) y `expiracion > fecha_emision`.
4. Índice compuesto en `('reserva', 'tipo_qr', 'usado')` y en `'token_qr'` para búsquedas en `O(1)` / índice único al escanear.

### 4.2 Reglas de Vigencia (`fecha_emision` y `expiracion`)
- **QR de `ENTRADA`**:
  - Se emite únicamente cuando `reserva.estado == 'CONFIRMADA'` (y el pago fue aprobado).
  - `expiracion = reserva.fecha_hora_fin` (no puede validarse un ingreso una vez concluido el horario reservado).
  - Ventana de validación al escanear: desde `reserva.fecha_hora_inicio - timedelta(minutes=15)` hasta `expiracion` (`reserva.fecha_hora_fin`). Si el arrendatario llega más de 15 minutos antes de `fecha_hora_inicio`, se rechaza temporalmente sin consumir el QR (`usado` permanece en `0`).
- **QR de `SALIDA`**:
  - Se emite automáticamente al validar con éxito el QR de `ENTRADA` (transición `CONFIRMADA -> EN_USO`).
  - `expiracion = max(reserva.fecha_hora_fin, now) + timedelta(hours=24)`: **Decisión crítica de diseño** — la expiración técnica del QR de salida debe ser posterior a `reserva.fecha_hora_fin` para permitir que un arrendatario con retraso (`RN-03`) pueda escanear su QR de salida, registrar `hora_salida_real` y recibir el cálculo de recargo en lugar de quedar bloqueado por token expirado.

### 4.3 Dependencias con Tablas Externas (Módulos 01, 02, 03 y 05)
El Módulo 04 consulta y actualiza de forma controlada los siguientes campos definidos en módulos previos/adyacentes, **sin agregar columnas ni alterar el esquema físico de `codigo_qr`**:
- **`reserva`** (`03-reservas`): `id_reserva`, `arrendatario_id`, `cochera_id`, `estado` (`SOLICITADA`, `CONFIRMADA`, `EN_USO`, `FINALIZADA`, `CANCELADA`, `EXPIRADA`), `fecha_hora_inicio`, `fecha_hora_fin`, `hora_salida_real`, `monto_base`, `monto_penalizacion`. Delega las transiciones de estado en los servicios `start_reservation_usage` y `finish_reservation` de `03-reservas`.
- **`cochera`** (`02-cocheras`): `id_cochera`, `arrendador_id`, `nombre`, `direccion`, `latitud`, `longitud`.
- **`usuario`** (`01-auth`): `id_usuario`, `nombre_completo`, `rol`, `estado_cuenta` (`'ACTIVA'`), `placas`, `modelo_vehiculo`.
- **Módulo 05 (`Pago`)**: Al emitir el QR de `ENTRADA` se verifica que la reserva esté `CONFIRMADA`; al calcular recargo por retraso en `SALIDA` (`monto_penalizacion > 0`), se invoca al servicio de ajuste de cobro del Módulo 05 para reflejar el total cobrado (`monto_base + monto_penalizacion`).

---

## 5. Contratos de API REST (Endpoints)

> **Convención de rutas:** Las rutas indicadas corresponden a la definición funcional del backend (`ParkShare_Backend/urls.py`). Todas requieren autenticación mediante token/sesión JWT/DRF en cabecera `Authorization: Bearer <token>` y respuestas en JSON (`Content-Type: application/json`). Los mensajes dirigidos al usuario se retornan en **español**.

### 5.1 `GET /reservas/{id}/qr`

Devuelve el código QR activo correspondiente al estado actual de la reserva (`ENTRADA` si la reserva está `CONFIRMADA`; `SALIDA` si está `EN_USO`).

- **Autenticación:** Requerida.
- **Permisos / Rol:** Usuario autenticado con rol `ARRENDATARIO` y propietario de la reserva (`reserva.arrendatario_id == request.user.id_usuario`).
- **Parámetros de ruta:**
  - `id` (`integer`): Identificador de la reserva (`reserva.id_reserva`).
- **Parámetros de consulta (Query Params):** Ninguno.
- **Cuerpo de petición:** No aplica.

#### Respuestas Esperadas

- **`200 OK`** — Reserva en estado `CONFIRMADA` (devuelve QR de `ENTRADA`) o `EN_USO` (devuelve QR de `SALIDA`). Incluye en `reserva` el resumen enriquecido retrocompatible (`monto_base`, `cochera` con coordenadas numéricas `float` para "Cómo llegar" en `ReservationQrScreen.jsx`, y `arrendatario` para `QrDisplayCard.jsx`):

```json
{
  "id_qr": 42,
  "reserva_id": 105,
  "token_qr": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "tipo_qr": "ENTRADA",
  "usado": false,
  "fecha_emision": "2026-11-19T14:05:00-06:00",
  "expiracion": "2026-11-19T18:00:00-06:00",
  "reserva": {
    "id_reserva": 105,
    "estado": "CONFIRMADA",
    "fecha_hora_inicio": "2026-11-19T16:00:00-06:00",
    "fecha_hora_fin": "2026-11-19T18:00:00-06:00",
    "monto_base": "80.00",
    "cochera": {
      "id_cochera": 10,
      "nombre": "Cochera Los Pinos",
      "direccion": "Av. Insurgentes Sur 1450",
      "latitud": 19.371245,
      "longitud": -99.178342
    },
    "arrendatario": {
      "id_usuario": 14,
      "nombre_completo": "Carlos Mendoza Ruiz",
      "placas": "ABC-123-D",
      "modelo_vehiculo": "Volkswagen Jetta 2020"
    }
  }
}
```

> **Nota de tipado e integridad ORM:** `cochera.latitud` y `cochera.longitud` se serializan como números JSON (`FloatField`) para compatibilidad directa con `buildExternalMapsUrl()` en `ParkShareApp/src/services/qrService.ts`. Al obtener la reserva con `select_related('arrendatario', 'cochera')`, estos campos no agregan consultas SQL adicionales (`assertNumQueries(3)`).

- **`401 Unauthorized`** — Sin credenciales válidas.
- **`403 Forbidden`** — El usuario no es `ARRENDATARIO` o intenta consultar el QR de una reserva ajena:

```json
{
  "error": "Forbidden",
  "detail": "No tienes permiso para consultar el código QR de esta reserva."
}
```

- **`404 Not Found`** — No existe la reserva `{id}`:

```json
{
  "error": "NotFound",
  "detail": "La reserva solicitada no existe."
}
```

- **`409 Conflict`** — La reserva está en `SOLICITADA` (pago pendiente), `CANCELADA`, `EXPIRADA` o `FINALIZADA`, o no existe un QR activo para el estado:

```json
{
  "error": "InvalidReservationState",
  "detail": "La reserva no cuenta con un código QR activo en su estado actual (SOLICITADA)."
}
```

---

### 5.2 `POST /reservas/validar`

Valida un token QR escaneado por el arrendador para autorizar la entrada (`CONFIRMADA -> EN_USO`) o confirmar la salida (`EN_USO -> FINALIZADA`).

- **Autenticación:** Requerida.
- **Permisos / Rol:** Usuario autenticado con rol `ARRENDADOR` (cuenta activa y verificada según `RN-08`), dueño de la cochera asociada a la reserva del QR.
- **Cuerpo de petición (Request Payload JSON):**

```json
{
  "token": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
}
```

#### Reglas de Validación de Entrada
- `token`: Obligatorio, cadena de 64 caracteres hexadecimales (`^[0-9a-fA-F]{64}$`). Se normaliza a minúsculas antes de buscar.

#### Respuestas Esperadas

- **`200 OK` (Caso `tipo_qr == 'ENTRADA'`)** — Marca `usado = true`, pasa reserva a `'EN_USO'` y emite el QR de `'SALIDA'` (por seguridad, el endpoint devuelve `qr_salida_emitido: true` **sin exponer el `token_qr` de salida al arrendador**, pues dicho token pertenece al arrendatario para presentarlo al salir conforme a `RN-06`):

```json
{
  "mensaje": "Entrada autorizada. La reserva ahora se encuentra en uso.",
  "tipo_qr": "ENTRADA",
  "estado_reserva": "EN_USO",
  "qr_salida_emitido": true,
  "verificacion": {
    "reserva_id": 105,
    "fecha_hora_inicio": "2026-11-19T16:00:00-06:00",
    "fecha_hora_fin": "2026-11-19T18:00:00-06:00",
    "arrendatario": {
      "id_usuario": 14,
      "nombre_completo": "Carlos Mendoza Ruiz",
      "placas": "ABC-123-D",
      "modelo_vehiculo": "Volkswagen Jetta 2020"
    }
  }
}
```

- **`200 OK` (Caso `tipo_qr == 'SALIDA'`)** — Marca `usado = true`, registra `hora_salida_real = now()`, aplica `RN-02` (10 min de tolerancia) y `RN-03` ($10.00 MXN/min adicional) y pasa la reserva a `'FINALIZADA'`:

```json
{
  "mensaje": "Salida confirmada. Reserva finalizada con recargo por retraso.",
  "tipo_qr": "SALIDA",
  "estado_reserva": "FINALIZADA",
  "verificacion": {
    "reserva_id": 105,
    "fecha_hora_inicio": "2026-11-19T16:00:00-06:00",
    "fecha_hora_fin": "2026-11-19T18:00:00-06:00",
    "hora_salida_real": "2026-11-19T18:20:00-06:00",
    "tolerancia_minutos": 10,
    "minutos_retraso": 10,
    "monto_base": "80.00",
    "monto_penalizacion": "100.00",
    "monto_total": "180.00",
    "arrendatario": {
      "id_usuario": 14,
      "nombre_completo": "Carlos Mendoza Ruiz",
      "placas": "ABC-123-D",
      "modelo_vehiculo": "Volkswagen Jetta 2020"
    }
  }
}
```

- **`400 Bad Request`** — Campo `token` ausente, vacío o no cumple formato SHA-256 (64 caracteres hexadecimales):

```json
{
  "error": "ValidationError",
  "detail": "El token QR debe ser una cadena hexadecimal válida de 64 caracteres."
}
```

- **`401 Unauthorized`** — Usuario no autenticado.
- **`403 Forbidden`** — Usuario no es `ARRENDADOR`, su cuenta está inactiva/pendiente (`RN-08`), o la cochera de la reserva no pertenece al arrendador autenticado:

```json
{
  "error": "Forbidden",
  "detail": "No tienes permiso para validar códigos QR de una cochera que no te pertenece."
}
```

- **`404 Not Found`** — El token QR no existe en la base de datos:

```json
{
  "error": "NotFound",
  "detail": "Código QR no encontrado o inválido."
}
```

- **`409 Conflict`** — El código QR ya fue utilizado (`usado == 1`), la reserva fue cancelada/expirada o no coincide con el estado esperado (`ENTRADA` requiere `CONFIRMADA`; `SALIDA` requiere `EN_USO`), es demasiado pronto para el ingreso (`now < fecha_hora_inicio - 15 min`), o excedió la tolerancia de llegada (`now > fecha_hora_inicio + 20 min`, devolviendo `error: "LateEntryCancelled"` con `estado_reserva: "CANCELADA"` y `monto_penalizacion: "100.00"`):

```json
{
  "error": "QRAlreadyUsed",
  "detail": "Este código QR ya fue utilizado previamente."
}
```

- **`410 Gone`** — El código QR está vigente en estado (`usado == 0`) pero superó su `expiracion` (`now > expiracion`):

```json
{
  "error": "QRExpired",
  "detail": "El código QR ha expirado y ya no es válido."
}
```

---

### 5.3 `GET /reservas/simular-qr`

Devuelve la lista paginada de códigos QR activos (`usado = false`, `expiracion > now`) pertenecientes a las cocheras del arrendador autenticado que se encuentran listos para escanear (`tipo_qr = 'ENTRADA'` con `reserva.estado == 'CONFIRMADA'`, o `tipo_qr = 'SALIDA'` con `reserva.estado == 'EN_USO'`), alimentando el bloque *"Pruebas rápidas de escaneo"* de `QrScannerScreen.jsx` (`MockQrDemoOption`).

- **Autenticación:** Requerida.
- **Permisos / Rol:** Usuario autenticado con rol `ARRENDADOR` y `estado_cuenta == 'ACTIVA'` (`IsActiveLandlord`).
- **Parámetros de consulta (Query Params):**
  - `tipo_qr` (`string`, opcional): `'ENTRADA'` o `'SALIDA'` (insensible a mayúsculas; si se envía un valor distinto retorna `400 Bad Request`).
  - `page`, `page_size` (opcionales, paginación estándar DRF `StandardPagination`).
- **Ordenamiento:** Por `reserva.fecha_hora_inicio` ascendente y `id_qr` ascendente (priorizando los escaneos más próximos).
- **Cuerpo de petición:** No aplica.

#### Respuestas Esperadas

- **`200 OK`** — Lista paginada con los campos exactos requeridos por el frontend (`MockQrDemoOption` extendido):

```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id_qr": 42,
      "reserva_id": 105,
      "tipo_qr": "ENTRADA",
      "token_qr": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
      "cochera_nombre": "Cochera Los Pinos",
      "placas": "ABC-123-D",
      "modelo_vehiculo": "Volkswagen Jetta 2020",
      "nombre_arrendatario": "Carlos Mendoza Ruiz",
      "estado_reserva": "CONFIRMADA",
      "fecha_hora_inicio": "2026-11-24T16:00:00-06:00",
      "fecha_hora_fin": "2026-11-24T18:00:00-06:00",
      "expiracion": "2026-11-24T18:00:00-06:00"
    },
    {
      "id_qr": 43,
      "reserva_id": 104,
      "tipo_qr": "SALIDA",
      "token_qr": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
      "cochera_nombre": "Cochera Los Pinos",
      "placas": "XYZ-789-A",
      "modelo_vehiculo": "Honda Civic 2023",
      "nombre_arrendatario": "Jorge Luis Hernández",
      "estado_reserva": "EN_USO",
      "fecha_hora_inicio": "2026-11-24T12:00:00-06:00",
      "fecha_hora_fin": "2026-11-24T14:00:00-06:00",
      "expiracion": "2026-11-25T14:00:00-06:00"
    }
  ]
}
```

- **`400 Bad Request`** — Valor de `tipo_qr` distinto de `ENTRADA` o `SALIDA`.
- **`401 Unauthorized`** — Sin credenciales válidas.
- **`403 Forbidden`** — Usuario no es `ARRENDADOR` o su cuenta no está `ACTIVA` (`RN-08`).

---

### 5.4 `POST /reservas/simular-qr`

Ejecuta la simulación directa de escaneo QR para el arrendador autenticado, resolviendo automáticamente el código QR activo según los parámetros opcionales enviados y devolviendo la misma estructura `QrValidationResponse` de `POST /reservas/validar`.

- **Autenticación:** Requerida.
- **Permisos / Rol:** Usuario autenticado con rol `ARRENDADOR` y `estado_cuenta == 'ACTIVA'` (`IsActiveLandlord`), propietario de la cochera de la reserva.
- **Cuerpo de petición (Request Payload JSON — todos los campos son opcionales):**

```json
{
  "reserva_id": 105,
  "tipo_qr": "ENTRADA",
  "respetar_ventana": false
}
```

*(Alternativas válidas: `{"token": "<sha256>"}`, `{"reserva_id": 105}`, `{"tipo_qr": "SALIDA"}`, o `{}` para tomar el primer QR activo disponible).*

#### Reglas de Resolución de Target y Simulación
1. **Resolución por `token`**: Si se envía `token`, valida el formato SHA-256 (`400` si inválido), localiza el `CodigoQR` (`404` si no existe) y valida que la cochera pertenezca al arrendador (`403` si es ajena). Si además se proporcionó `reserva_id` o `tipo_qr` y no coinciden con ese `CodigoQR`, responde `409 Conflict` / `400 Bad Request`.
2. **Resolución por `reserva_id` (con o sin `tipo_qr`)**: Busca la `Reserva` (`404` si no existe; `403` si pertenece a otro arrendador). Si no se envía `tipo_qr`, infiere `ENTRADA` si `reserva.estado == 'CONFIRMADA'` o `SALIDA` si `reserva.estado == 'EN_USO'` (`409 Conflict` para cualquier otro estado). Busca el `CodigoQR` correspondiente (`409 Conflict` si ya fue usado o no existe).
3. **Resolución automática (`tipo_qr` sin `reserva_id`, o `{}` vacío)**: Selecciona dentro de las cocheras del arrendador el primer `CodigoQR` activo (`usado = False`, `expiracion > now`) cuyo tipo coincida con `tipo_qr` (cuando se envió) y cuya reserva esté lista (`CONFIRMADA` para `ENTRADA`, `EN_USO` para `SALIDA`), ordenado por `reserva.fecha_hora_inicio` ascendente y `id_qr` ascendente. Si no hay QRs elegibles, responde `404 Not Found` (`error: "NoActiveQRToSimulate"`).
4. **Comportamiento para reservas futuras en simulación (`respetar_ventana`)**:
   - Por defecto (`respetar_ventana = false`): si `tipo_qr == 'ENTRADA'` y `now < reserva.fecha_hora_inicio - 15 min` (reserva programada para más tarde hoy o días próximos) y `now <= qr.expiracion`, la simulación utiliza `effective_now = reserva.fecha_hora_inicio` para ejecutar la validación de entrada (`CONFIRMADA -> EN_USO` + emisión de QR `SALIDA`). En `SALIDA`, la función subyacente ya considera `max(now, reserva.fecha_hora_inicio)`, permitiendo probar el ciclo completo de entrada y salida de forma inmediata.
   - Si `respetar_ventana = true`: aplica exactamente la misma restricción anticipada de 15 minutos que `POST /reservas/validar` (`409 Conflict` si faltan más de 15 min para `fecha_hora_inicio`).
   - Si `now > reserva.fecha_hora_inicio + 20 min` al simular `ENTRADA`, se mantiene la regla real de cancelación automática por llegada tardía (`LateEntryCancelled`, `409 Conflict`).

#### Respuestas Esperadas

- **`200 OK` (Simulación de `ENTRADA`)** — Estructura compatible con `QrValidationResponse` más bandera informativa `"simulado": true`:

```json
{
  "mensaje": "Entrada autorizada. La reserva ahora se encuentra en uso.",
  "tipo_qr": "ENTRADA",
  "estado_reserva": "EN_USO",
  "qr_salida_emitido": true,
  "simulado": true,
  "verificacion": {
    "reserva_id": 105,
    "fecha_hora_inicio": "2026-11-24T16:00:00-06:00",
    "fecha_hora_fin": "2026-11-24T18:00:00-06:00",
    "arrendatario": {
      "id_usuario": 14,
      "nombre_completo": "Carlos Mendoza Ruiz",
      "placas": "ABC-123-D",
      "modelo_vehiculo": "Volkswagen Jetta 2020"
    }
  }
}
```

- **`200 OK` (Simulación de `SALIDA`)** — Estructura compatible con `QrValidationResponse` más bandera informativa `"simulado": true`:

```json
{
  "mensaje": "Salida confirmada. Reserva finalizada sin recargos.",
  "tipo_qr": "SALIDA",
  "estado_reserva": "FINALIZADA",
  "simulado": true,
  "verificacion": {
    "reserva_id": 105,
    "fecha_hora_inicio": "2026-11-24T16:00:00-06:00",
    "fecha_hora_fin": "2026-11-24T18:00:00-06:00",
    "hora_salida_real": "2026-11-24T16:00:00-06:00",
    "tolerancia_minutos": 10,
    "minutos_retraso": 0,
    "monto_base": "80.00",
    "monto_penalizacion": "0.00",
    "monto_total": "80.00",
    "arrendatario": {
      "id_usuario": 14,
      "nombre_completo": "Carlos Mendoza Ruiz",
      "placas": "ABC-123-D",
      "modelo_vehiculo": "Volkswagen Jetta 2020"
    }
  }
}
```

- **`400 Bad Request`** — `token` con formato inválido, `tipo_qr` inválido o `reserva_id` no entero positivo.
- **`401 Unauthorized`** — Usuario no autenticado.
- **`403 Forbidden`** — Usuario no es `ARRENDADOR` activo (`RN-08`) o intenta simular una reserva de una cochera ajena.
- **`404 Not Found`** — Reserva inexistente, token inexistente, o ausencia de QRs activos para simular (`error: "NoActiveQRToSimulate"`).
- **`409 Conflict`** — QR ya usado (`QRAlreadyUsed`), estado de reserva incompatible (`InvalidReservationState`), incompatibilidad entre `token` y `reserva_id`/`tipo_qr`, entrada tardía > 20 min (`LateEntryCancelled`), o llegada anticipada con `respetar_ventana: true`.
- **`410 Gone`** — El QR superó su `expiracion` (`QRExpired`).

---

## 6. Escenarios de Prueba para TDD

### 6.1 Casos Felices (Happy Path)
1. **Generación automática de token SHA-256 (`CU-06`)**: Al confirmar pago de una reserva (`SOLICITADA -> CONFIRMADA`), se crea un `CodigoQR` con `tipo_qr='ENTRADA'`, `usado=False`, `len(token_qr) == 64`, solo caracteres hexadecimales y `expiracion == reserva.fecha_hora_fin`.
2. **Consulta de QR por el arrendatario (`GET /reservas/{id}/qr`)**:
   - En estado `CONFIRMADA`, retorna `200 OK` con el QR `ENTRADA`.
   - En estado `EN_USO`, retorna `200 OK` con el QR `SALIDA`.
3. **Validación de llegada (`CU-08`, `POST /reservas/validar` con `ENTRADA`)**: Arrendador dueño de la cochera escanea el token de `ENTRADA` dentro de la ventana vigente -> retorna `200 OK`, `codigo_qr_entrada.usado == True`, `reserva.estado == 'EN_USO'`, se crea `CodigoQR` de `SALIDA` (`usado=False`) y la respuesta incluye `nombre_completo`, `placas`, `modelo_vehiculo` y horario reservado (sin filtrar el `token_qr` de salida al arrendador).
4. **Validación de salida a tiempo (`RN-06`, `RN-02`)**: Arrendador escanea QR de `SALIDA` antes de `fecha_hora_fin` -> `200 OK`, `reserva.estado == 'FINALIZADA'`, `hora_salida_real` registrada, `minutos_retraso == 0`, `monto_penalizacion == Decimal('0.00')`.
5. **Validación de salida dentro de tolerancia (`RN-02`)**: Arrendador escanea QR de `SALIDA` exactamente a `fecha_hora_fin + 10 minutos` (y a `fecha_hora_fin + 9 min 59 s`) -> `200 OK`, `minutos_retraso == 0`, `monto_penalizacion == Decimal('0.00')`.
6. **Validación de salida con retraso (`RN-03`)**:
   - Salida a `fecha_hora_fin + 11 minutos` (1 minuto fuera de tolerancia) -> `minutos_retraso == 1`, `monto_penalizacion == Decimal('10.00')`.
   - Salida a `fecha_hora_fin + 20 minutos` (10 minutos fuera de tolerancia, caso ejemplo de la Constitución) -> `minutos_retraso == 10`, `monto_penalizacion == Decimal('100.00')`.

### 6.2 Casos de Error y Validación
1. **Token malformado en `POST /reservas/validar`**: Token vacío, nulo, menor a 64 caracteres o con caracteres no hexadecimales (`"xyz"`) -> `400 Bad Request`.
2. **Token inexistente**: Token SHA-256 válido en formato pero inexistente en BD -> `404 Not Found`.
3. **Acceso sin autenticación**: Peticiones a `GET /reservas/{id}/qr` o `POST /reservas/validar` sin token -> `401 Unauthorized`.
4. **Autorización cruzada en `GET /reservas/{id}/qr`**: Otro `ARRENDATARIO` o un `ARRENDADOR` intenta obtener el QR de una reserva ajena -> `403 Forbidden`.
5. **Autorización cruzada en `POST /reservas/validar`**: Un `ARRENDADOR` intenta escanear el QR de una cochera que pertenece a otro arrendador, o un `ARRENDATARIO` intenta llamar a `/reservas/validar` -> `403 Forbidden`.
6. **Arrendador no verificado (`RN-08`)**: Arrendador con cuenta pendiente o inactiva (`estado_cuenta != 'ACTIVA'`) intenta validar QR -> `403 Forbidden`.
7. **Consulta de QR en reserva no confirmada / finalizada / cancelada / expirada**: `GET /reservas/{id}/qr` con reserva en `SOLICITADA`, `CANCELADA`, `EXPIRADA` o `FINALIZADA` -> `409 Conflict`.
8. **Reuso de QR ya consumido**: Reenviar el mismo `token` de `ENTRADA` o `SALIDA` que ya tiene `usado == True` -> `409 Conflict` sin alterar el estado de la reserva.
9. **QR expirado**: Escanear QR de `ENTRADA` cuando `now > reserva.fecha_hora_fin` -> `410 Gone`, `usado` se mantiene en `False` y la reserva no pasa a `EN_USO`.
10. **Reserva cancelada**: Intentar validar QR de `ENTRADA` cuya reserva pasó a `CANCELADA` -> `409 Conflict`.

### 6.3 Casos Límite (Edge Cases)
1. **Concurrencia en escaneo simultáneo**: Dos peticiones concurrentes a `POST /reservas/validar` con el mismo token usando `select_for_update()` -> exactamente una obtiene `200 OK` y la otra `409 Conflict`, sin duplicar la emisión del QR de `SALIDA` ni recalcular dos veces la salida.
2. **Frontera exacta de tolerancia (`RN-02`)**:
   - `fecha_hora_fin + 10 min 00 s` -> `$0.00 MXN`.
   - `fecha_hora_fin + 10 min 01 s` -> `1 min` cobrable (`$10.00 MXN`).
3. **Salida muy tardía (ej. +3 horas sobre `fecha_hora_fin`)**: El QR de `SALIDA` no debe marcarse expirado a las `fecha_hora_fin`, permitiendo finalizar la estancia y cobrar `170 * $10.00 = $1,700.00 MXN` sin quedar bloqueado.
4. **Llegada anticipada dentro y fuera de ventana**:
   - Llegada `10 min` antes de `fecha_hora_inicio` -> `200 OK` (dentro de ventana de cortesía de 15 min).
   - Llegada `2 horas` antes de `fecha_hora_inicio` -> `409 Conflict` (demasiado temprano; `usado` sigue en `False`).
5. **Integridad de unicidad en BD**: Intentar insertar directamente un segundo `CodigoQR` con el mismo `(reserva_id, tipo_qr)` o el mismo `token_qr` debe lanzar `IntegrityError`.

### 6.4 Escenarios de Integración Frontend y Simulación QR (Fase 5)

#### Casos Felices
1. **Payload enriquecido en `GET /reservas/{id}/qr`**:
   - La respuesta `200 OK` incluye `reserva.monto_base` (string decimal), `reserva.cochera` (`id_cochera`, `nombre`, `direccion`, `latitud`, `longitud` como números `float`) y `reserva.arrendatario` (`id_usuario`, `nombre_completo`, `placas`, `modelo_vehiculo`).
   - Se mantiene un conteo constante de consultas ORM (`assertNumQueries(3)`).
2. **Listado de QRs activos para simulación (`GET /reservas/simular-qr`)**:
   - Arrendador activo recibe `200 OK` con `count` y `results` conteniendo exclusivamente sus QRs activos no usados y no expirados (`CONFIRMADA + ENTRADA` y `EN_USO + SALIDA`), ordenados por `fecha_hora_inicio` ascendente, con los 12 campos esperados por el frontend (`id_qr`, `reserva_id`, `tipo_qr`, `token_qr`, `cochera_nombre`, `placas`, `modelo_vehiculo`, `nombre_arrendatario`, `estado_reserva`, `fecha_hora_inicio`, `fecha_hora_fin`, `expiracion`).
   - El filtro `?tipo_qr=ENTRADA` o `?tipo_qr=SALIDA` acota correctamente los resultados.
3. **Simulación directa por `tipo_qr`, `reserva_id` o `token` (`POST /reservas/simular-qr`)**:
   - Envío de `{"tipo_qr": "ENTRADA"}` sobre una reserva confirmada programada para más tarde en el día (`now < fecha_hora_inicio - 15 min`, con `respetar_ventana=false` por defecto) -> `200 OK`, `"simulado": true`, `estado_reserva == "EN_USO"`, emite QR de `SALIDA`, no expone el `token_qr` de salida en la respuesta y refleja el cambio al volver a llamar `GET /reservas/simular-qr` (desaparece el QR `ENTRADA` y aparece el `SALIDA`).
   - Envío inmediato de `{"reserva_id": <id>}` o `{"tipo_qr": "SALIDA"}` -> `200 OK`, `"simulado": true`, `estado_reserva == "FINALIZADA"`, `hora_salida_real >= fecha_hora_inicio`, calcula `RN-02`/`RN-03`, y desaparece de `GET /reservas/simular-qr`.
   - Envío de `{"token": "<sha256>"}` ejecuta la misma validación y retorna `200 OK` con `"simulado": true`.

#### Casos de Error, Validación y Límite
1. **Aislamiento de arrendador y control de roles (`GET` y `POST /reservas/simular-qr`)**:
   - Sin token -> `401 Unauthorized`.
   - `ARRENDATARIO`, `ADMIN` o `ARRENDADOR` pendiente/suspendido (`RN-08`) -> `403 Forbidden`.
   - Arrendador A no ve en `GET /reservas/simular-qr` los QRs de Arrendador B, y si intenta `POST /reservas/simular-qr` con `reserva_id` o `token` de Arrendador B -> `403 Forbidden`.
2. **Parámetros inválidos**:
   - `GET /reservas/simular-qr?tipo_qr=OTRO` -> `400 Bad Request`.
   - `POST /reservas/simular-qr` con `token` no hexadecimal, `tipo_qr` desconocido o `reserva_id <= 0` -> `400 Bad Request`.
   - `POST /reservas/simular-qr` enviando `token` y `reserva_id` de reservas distintas -> `409 Conflict`.
3. **Sin QRs disponibles para simular**: Si el arrendador no tiene QRs activos elegibles del tipo solicitado -> `POST /reservas/simular-qr` responde `404 Not Found` (`NoActiveQRToSimulate`) y `GET /reservas/simular-qr` devuelve `count: 0, results: []`.
4. **Modo estricto `respetar_ventana: true`**: Al enviar `{"reserva_id": <id>, "respetar_ventana": true}` sobre reserva futura (`now < fecha_hora_inicio - 15 min`) -> `409 Conflict` sin consumir el QR.
5. **Tolerancia tardía (`> inicio + 20 min`) y expiración en simulación**:
   - Simular `ENTRADA` cuando `now > fecha_hora_inicio + 20 min` mantiene `LateEntryCancelled` (`409 Conflict`, cancelación con `$100.00 MXN`).
   - Simular sobre QR expirado (`now > expiracion`) responde `410 Gone`.
   - Simular dos veces la misma `reserva_id` con `tipo_qr="ENTRADA"` -> la segunda llamada responde `409 Conflict` (`QRAlreadyUsed`).
