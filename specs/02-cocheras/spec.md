# Especificación Técnica — Módulo 02: Registro, Validación y Búsqueda Geográfica de Cocheras (`02-cocheras`)

---

## 1. Resumen y Alcance

Este módulo gestiona el ciclo de vida completo de las cocheras en **ParkShare_Backend**: alta por parte de arrendadores autorizados (**CU-02**), configuración de horarios y tarifas (**CU-03**), validación administrativa antes de su publicación (**RN-10**), gestión de estado/disponibilidad por el propietario y búsqueda geoespacial para arrendatarios (**CU-04**).

### Alcance incluido
- Registro atómico de cochera (`cochera`), fotografías (`foto_cochera`) y franjas horarias por día de la semana (`disponibilidad`).
- Bloqueo de publicación para cuentas que no sean `ARRENDADOR` con `estado_cuenta = 'ACTIVA'` (**RN-08**).
- Validación de superficie, capacidad, tarifa, coordenadas geográficas y formatos de imagen (**RN-09**).
- Flujo de consulta, aprobación y rechazo de publicaciones por el administrador (**RN-10**).
- Búsqueda de cocheras aprobadas cercanas a unas coordenadas (`lat`, `lng`) usando fórmula de Haversine en MySQL, con ordenamiento por `cercania`, `precio` o `disponibilidad` (**CU-04**).
- Consulta de cocheras propias del arrendador, detalle de cochera y edición parcial (tarifas, horarios, activación/inactivación temporal `APROBADA <-> INACTIVA`).

### Fuera de alcance en este módulo (puntos de integración)
- La tabla `resena` pertenece a un módulo posterior; `GET /cocheras/{id}` incluye `calificacion_promedio` (`null` por defecto) y `total_resenas` (`0` por defecto) mediante un servicio desacoplado que será alimentado al implementar `resena`.
- La ocupación en tiempo real para `orden=disponibilidad` se integra con la tabla `reserva` del Módulo `03-reservas` (mientras no exista `reserva`, la disponibilidad instantánea evalúa la franja horaria vigente y retorna `espacios_disponibles = espacios_totales` si está abierta o `0` si está cerrada).

---

## 2. Casos de Uso Cubiertos (CIM)

| ID | Nombre | Actor | Descripción |
| :--- | :--- | :--- | :--- |
| **CU-02** | Registrar cochera | Arrendador (`ACTIVA`) | Registra una nueva cochera indicando nombre, dirección, coordenadas (`latitud`, `longitud`), fotografías, superficie en $m^2$, capacidad de espacios, tipo de vehículo permitido y tarifa por hora. Incluye obligatoriamente **CU-03**. Nace en estado `PENDIENTE`. |
| **CU-03** | Definir horario y tarifa | Arrendador (`ACTIVA`) | Define las franjas horarias semanales de disponibilidad (`dia_semana`, `hora_inicio`, `hora_fin`) y `tarifa_hora` en pesos mexicanos (MXN), tanto al crear la cochera como al actualizarla. |
| **CU-04** | Buscar cocheras cercanas | Arrendatario (`ACTIVA`) | Consulta cocheras en estado `APROBADA` enviando su ubicación (`lat`, `lng`), obteniendo distancia calculada y ordenando resultados por `cercania` (por defecto), `precio` o `disponibilidad`. |

---

## 3. Reglas de Negocio Aplicables (`RN-XX`)

| Regla | Aplicación concreta en el módulo |
| :--- | :--- |
| **RN-08** (Cuenta activa de arrendador) | Únicamente usuarios autenticados con `rol = 'ARRENDADOR'` y `estado_cuenta = 'ACTIVA'` pueden registrar (`POST /cocheras`) o administrar (`GET /cocheras/mis-cocheras`, `PATCH /cocheras/{id}`) cocheras. Arrendadores en estado `PENDIENTE`, `RECHAZADA` o `SUSPENDIDA`, así como `ARRENDATARIO`, reciben `403 Forbidden`. |
| **RN-09** (Datos de la cochera) | Toda cochera debe incluir al menos **1 fotografía** en formato `JPG`, `PNG` o `WEBP` (`foto_cochera`), especificar su superficie `metros_cuadrados > 0`, especificar `espacios_totales >= 1`, `tarifa_hora > 0` y al menos **1 franja horaria** válida en `disponibilidad` (`CU-03`). |
| **RN-10** (Validación de la publicación) | Toda cochera recién creada nace en `estado_cochera = 'PENDIENTE'`. Solo un `ADMIN` (`GET /admin/cocheras` para consultar la bandeja de revisión y `PATCH /admin/cocheras/{id}/validar` para resolver) puede cambiarla a `APROBADA` (si cumple condiciones para resguardar vehículos `AUTO`, `MOTO` o `AMBOS`) o `RECHAZADA`. Un arrendador nunca puede autoaprobar su cochera mediante `PATCH /cocheras/{id}`. |

### Reglas de Máquina de Estados de `Cochera`
- **Estados permitidos**: `PENDIENTE`, `APROBADA`, `RECHAZADA`, `INACTIVA`.
- **Transiciones válidas**:
  - `PENDIENTE -> APROBADA`: Solo `ADMIN` (`PATCH /admin/cocheras/{id}/validar`).
  - `PENDIENTE -> RECHAZADA`: Solo `ADMIN` (`PATCH /admin/cocheras/{id}/validar`).
  - `APROBADA -> INACTIVA`: Solo `ARRENDADOR` propietario (`PATCH /cocheras/{id}`) para pausar temporalmente su publicación.
  - `INACTIVA -> APROBADA`: Solo `ARRENDADOR` propietario (`PATCH /cocheras/{id}`) para reactivar una cochera previamente aprobada.
- **Transiciones prohibidas** (devuelven `403 Forbidden` o `409 Conflict`):
  - `PENDIENTE -> APROBADA` por el arrendador (`403 Forbidden`).
  - `RECHAZADA -> APROBADA` o `RECHAZADA -> INACTIVA` por el arrendador (`403 Forbidden` / `409 Conflict`).
  - Re-validación por `ADMIN` cuando `estado_cochera != 'PENDIENTE'` (`409 Conflict`).

---

## 4. Modelo de Datos (Diccionario de Datos / PSM)

### 4.1 Tabla `cochera`

| Columna (`db_column`) | Tipo SQL / MySQL | Nulidad | Restricciones / Default | Descripción |
| :--- | :--- | :--- | :--- | :--- |
| `id_cochera` | `BIGINT AUTO_INCREMENT` | NO NULO | `PK` | Identificador único de la cochera. |
| `arrendador_id` | `BIGINT` | NO NULO | `FK -> usuario.id_usuario` (`ON DELETE CASCADE`), `INDEX` | Arrendador propietario de la cochera. |
| `nombre` | `VARCHAR(120)` | NO NULO | No vacío (ej. `"Cochera Los Pinos"`) | Título visible de la cochera. |
| `direccion` | `VARCHAR(255)` | NO NULO | No vacío | Dirección completa (calle, número, colonia, C.P.). |
| `latitud` | `DECIMAL(10, 8)` | NO NULO | Rango `-90.00000000` a `90.00000000` | Coordenada geográfica de latitud. |
| `longitud` | `DECIMAL(11, 8)` | NO NULO | Rango `-180.00000000` a `180.00000000` | Coordenada geográfica de longitud. |
| `metros_cuadrados` | `DECIMAL(6, 2)` | NO NULO | `> 0` | Superficie total disponible en $m^2$ (`RN-09`). |
| `espacios_totales` | `INT` | NO NULO | `>= 1` | Cantidad total de cajones simultáneos (`RN-09`). |
| `tarifa_hora` | `DECIMAL(8, 2)` | NO NULO | `> 0` | Precio por hora en MXN (`CU-03`). |
| `tipo_vehiculo` | `ENUM('AUTO', 'MOTO', 'AMBOS')` | NO NULO | Default `'AMBOS'` | Tipos de vehículos aceptados (`RN-10`). |
| `estado_cochera` | `ENUM('PENDIENTE', 'APROBADA', 'RECHAZADA', 'INACTIVA')` | NO NULO | Default `'PENDIENTE'`, `INDEX` | Estado administrativo y operativo. |

> **Nota sobre columna `motivo_rechazo` en `cochera`:** El Diccionario de Datos oficial de `cochera` no define columna `motivo_rechazo`. Por fidelidad al esquema (`AGENTS.md`), `cochera` mantiene exactamente estas 11 columnas; en `PATCH /admin/cocheras/{id}/validar`, si el admin envía un `motivo` opcional al rechazar, se devuelve en la respuesta JSON de confirmación/auditoría sin alterar el esquema guardado (a menos que el usuario autorice agregar una columna `motivo_rechazo TEXT NULL`).

### 4.2 Tabla `foto_cochera`

| Columna (`db_column`) | Tipo SQL / MySQL | Nulidad | Restricciones / Default | Descripción |
| :--- | :--- | :--- | :--- | :--- |
| `id_foto` | `BIGINT AUTO_INCREMENT` | NO NULO | `PK` | Identificador único de la fotografía. |
| `cochera_id` | `BIGINT` | NO NULO | `FK -> cochera.id_cochera` (`ON DELETE CASCADE`), `INDEX` | Cochera asociada. |
| `url_foto` | `VARCHAR(255)` | NO NULO | Extensión `.jpg`, `.jpeg`, `.png` o `.webp` | URL o ruta de almacenamiento de la imagen. |

### 4.3 Tabla `disponibilidad`

| Columna (`db_column`) | Tipo SQL / MySQL | Nulidad | Restricciones / Default | Descripción |
| :--- | :--- | :--- | :--- | :--- |
| `id_disponibilidad` | `BIGINT AUTO_INCREMENT` | NO NULO | `PK` | Identificador único de la franja horaria. |
| `cochera_id` | `BIGINT` | NO NULO | `FK -> cochera.id_cochera` (`ON DELETE CASCADE`), `INDEX` | Cochera asociada. |
| `dia_semana` | `ENUM('L', 'M', 'X', 'J', 'V', 'S', 'D')` | NO NULO | `INDEX` (`L`=Lunes ... `D`=Domingo) | Día de la semana aplicable. |
| `hora_inicio` | `TIME` | NO NULO | Formato `HH:MM[:SS]` | Hora de apertura de la franja. |
| `hora_fin` | `TIME` | NO NULO | `hora_fin > hora_inicio` | Hora de cierre de la franja. |

> **Regla de integridad en franjas horarias**: No se permiten franjas superpuestas para una misma `cochera_id` y el mismo `dia_semana` (dos franjas `A` y `B` en el mismo día se traslapan si `A.hora_inicio < B.hora_fin` y `A.hora_fin > B.hora_inicio`). Para indicar operación continua hasta el final del día se utiliza `23:59:59` (o `23:59:00`).

### 4.4 Diagrama ER Simplificado
```
usuario (1) ──────< (N) cochera
cochera (1) ──────< (N) foto_cochera
cochera (1) ──────< (N) disponibilidad
```

---

## 5. Contratos de API REST (Endpoints)

### 5.1 `POST /cocheras`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsActiveLandlord`: `rol == 'ARRENDADOR'` y `estado_cuenta == 'ACTIVA'`).
- **Descripción**: Registra una cochera con sus fotos y horarios de disponibilidad (**CU-02**, **CU-03**, **RN-08**, **RN-09**). Siempre inicia en `estado_cochera = 'PENDIENTE'`.

#### Request Payload (`application/json`)
```json
{
  "nombre": "Cochera Los Pinos",
  "direccion": "Av. Insurgentes Sur 1450, Col. Actipan, C.P. 03230",
  "latitud": 19.37124500,
  "longitud": -99.17834200,
  "metros_cuadrados": "35.50",
  "espacios_totales": 2,
  "tarifa_hora": "45.00",
  "tipo_vehiculo": "AMBOS",
  "fotos": [
    "https://storage.parkshare.mx/cocheras/pinos_1.jpg",
    "https://storage.parkshare.mx/cocheras/pinos_2.webp"
  ],
  "disponibilidades": [
    { "dia_semana": "L", "hora_inicio": "07:00", "hora_fin": "22:00" },
    { "dia_semana": "M", "hora_inicio": "07:00", "hora_fin": "22:00" }
  ]
}
```
*(Si el cliente envía `multipart/form-data`, el backend puede aceptar archivos en `fotos`, validar formato `JPG/PNG/WEBP` y guardar su ruta `<= 255` chars en `foto_cochera.url_foto`.)*

#### Respuestas
- **`201 Created`**:
```json
{
  "id_cochera": 10,
  "arrendador_id": 2,
  "nombre": "Cochera Los Pinos",
  "direccion": "Av. Insurgentes Sur 1450, Col. Actipan, C.P. 03230",
  "latitud": "19.37124500",
  "longitud": "-99.17834200",
  "metros_cuadrados": "35.50",
  "espacios_totales": 2,
  "tarifa_hora": "45.00",
  "tipo_vehiculo": "AMBOS",
  "estado_cochera": "PENDIENTE",
  "fotos": [
    { "id_foto": 1, "url_foto": "https://storage.parkshare.mx/cocheras/pinos_1.jpg" },
    { "id_foto": 2, "url_foto": "https://storage.parkshare.mx/cocheras/pinos_2.webp" }
  ],
  "disponibilidades": [
    { "id_disponibilidad": 1, "dia_semana": "L", "hora_inicio": "07:00:00", "hora_fin": "22:00:00" },
    { "id_disponibilidad": 2, "dia_semana": "M", "hora_inicio": "07:00:00", "hora_fin": "22:00:00" }
  ]
}
```
- **`400 Bad Request`**: Lista `fotos` vacía o extensión inválida, `metros_cuadrados <= 0`, `espacios_totales < 1`, `tarifa_hora <= 0`, coordenadas fuera de rango, `disponibilidades` vacía, `hora_fin <= hora_inicio`, o franjas traslapadas en el mismo día.
- **`403 Forbidden`**: Usuario no es `ARRENDADOR` o su `estado_cuenta != 'ACTIVA'` (`RN-08`).

---

### 5.2 `GET /cocheras?lat=&lng=&orden=`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsActiveAccount`).
- **Descripción**: Busca únicamente cocheras con `estado_cochera = 'APROBADA'` (**CU-04**). Calcula distancia geodésica mediante fórmula de Haversine ($R = 6371\text{ km}$):
  $$d = 6371 \cdot \arccos\big(\text{clamp}(\cos(\text{rad}(\text{lat}_1))\cos(\text{rad}(\text{lat}_2))\cos(\text{rad}(\text{lng}_2)-\text{rad}(\text{lng}_1)) + \sin(\text{rad}(\text{lat}_1))\sin(\text{rad}(\text{lat}_2)), -1, 1)\big)$$
- **Query Params**:
  - `lat` (obligatorio): float en `[-90.0, 90.0]`.
  - `lng` (obligatorio): float en `[-180.0, 180.0]`.
  - `orden` (opcional, default `cercania`):
    - `cercania`: menor `distancia_km` primero.
    - `precio`: menor `tarifa_hora` primero (desempate por `distancia_km`).
    - `disponibilidad`: mayor `espacios_disponibles_ahora` primero (desempate por `distancia_km`).
  - `radio_km` (opcional): float `> 0` para filtrar cocheras dentro de un radio máximo.
  - `tipo_vehiculo` (opcional): `AUTO` | `MOTO` (si se filtra por `AUTO`, devuelve cocheras `AUTO` y `AMBOS`; si `MOTO`, devuelve `MOTO` y `AMBOS`).

#### Respuestas
- **`200 OK`**:
```json
{
  "count": 1,
  "results": [
    {
      "id_cochera": 10,
      "nombre": "Cochera Los Pinos",
      "direccion": "Av. Insurgentes Sur 1450, Col. Actipan, C.P. 03230",
      "latitud": "19.37124500",
      "longitud": "-99.17834200",
      "distancia_km": 0.42,
      "tarifa_hora": "45.00",
      "espacios_totales": 2,
      "espacios_disponibles_ahora": 2,
      "abierta_ahora": true,
      "tipo_vehiculo": "AMBOS",
      "foto_principal": "https://storage.parkshare.mx/cocheras/pinos_1.jpg"
    }
  ]
}
```
- **`400 Bad Request`**: Falta `lat` o `lng`, formato no numérico, coordenadas fuera de rango o valor de `orden` inválido.

---

### 5.3 `GET /cocheras/mis-cocheras`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsActiveLandlord`).
- **Descripción**: Lista todas las cocheras pertenecientes al arrendador autenticado en cualquier estado (`PENDIENTE`, `APROBADA`, `INACTIVA`, `RECHAZADA`).
- **Query Params**:
  - `estado` (opcional): filtra por `estado_cochera`.
- **Respuestas**:
  - **`200 OK`**:
```json
{
  "count": 2,
  "results": [
    {
      "id_cochera": 10,
      "nombre": "Cochera Los Pinos",
      "direccion": "Av. Insurgentes Sur 1450, Col. Actipan, C.P. 03230",
      "tarifa_hora": "45.00",
      "espacios_totales": 2,
      "tipo_vehiculo": "AMBOS",
      "estado_cochera": "APROBADA",
      "foto_principal": "https://storage.parkshare.mx/cocheras/pinos_1.jpg"
    }
  ]
}
```
  - **`403 Forbidden`**: Usuario no es arrendador activo.

---

### 5.4 `GET /cocheras/{id}`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsActiveAccount`).
  - Las cocheras `APROBADA` son visibles para cualquier usuario autenticado activo.
  - Las cocheras en `PENDIENTE`, `RECHAZADA` o `INACTIVA` solo son visibles para su `arrendador` propietario o un `ADMIN`; cualquier otro usuario recibe `404 Not Found`.
- **Respuestas**:
  - **`200 OK`**:
```json
{
  "id_cochera": 10,
  "arrendador": {
    "id_usuario": 2,
    "nombre_completo": "Carlos Eduardo Ramírez Pérez"
  },
  "nombre": "Cochera Los Pinos",
  "direccion": "Av. Insurgentes Sur 1450, Col. Actipan, C.P. 03230",
  "latitud": "19.37124500",
  "longitud": "-99.17834200",
  "metros_cuadrados": "35.50",
  "espacios_totales": 2,
  "tarifa_hora": "45.00",
  "tipo_vehiculo": "AMBOS",
  "estado_cochera": "APROBADA",
  "calificacion_promedio": null,
  "total_resenas": 0,
  "fotos": [
    { "id_foto": 1, "url_foto": "https://storage.parkshare.mx/cocheras/pinos_1.jpg" }
  ],
  "disponibilidades": [
    { "id_disponibilidad": 1, "dia_semana": "L", "hora_inicio": "07:00:00", "hora_fin": "22:00:00" }
  ]
}
```
  - **`404 Not Found`**: No existe o no tiene visibilidad sobre el recurso.

---

### 5.5 `PATCH /cocheras/{id}`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsActiveLandlord`, propietario de la cochera).
- **Descripción**: Permite al arrendador propietario actualizar `nombre`, `tarifa_hora`, `espacios_totales`, `tipo_vehiculo`, `disponibilidades` (reemplazo atómico validando no traslape y `hora_fin > hora_inicio`), o cambiar `estado_cochera` **únicamente** entre `APROBADA <-> INACTIVA`.

#### Request Payload
```json
{
  "tarifa_hora": "50.00",
  "estado_cochera": "INACTIVA",
  "disponibilidades": [
    { "dia_semana": "L", "hora_inicio": "08:00", "hora_fin": "20:00" },
    { "dia_semana": "S", "hora_inicio": "09:00", "hora_fin": "18:00" }
  ]
}
```

#### Respuestas
- **`200 OK`**: Devuelve la representación actualizada completa de la cochera.
- **`400 Bad Request`**: Valores inválidos (`tarifa_hora <= 0`, `espacios_totales < 1`, franjas vacías/inválidas).
- **`403 Forbidden`**: Usuario no es el arrendador dueño, cuenta inactiva, o intento del arrendador de cambiar estado `PENDIENTE -> APROBADA` o `RECHAZADA -> APROBADA` (`RN-10`).
- **`409 Conflict`**: Transición de estado inválida desde `PENDIENTE` o `RECHAZADA` hacia `INACTIVA`/`APROBADA`.

---

### 5.6 `GET /admin/cocheras`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsAdminRole`).
- **Descripción**: Lista las cocheras para revisión administrativa (**RN-10**), ordenadas de más reciente a más antigua (`-id_cochera`).
- **Query Params**:
  - `estado` (opcional, default `PENDIENTE`): `PENDIENTE` | `APROBADA` | `RECHAZADA` | `INACTIVA` | `TODOS`.
  - `page`, `page_size` (paginación estándar).

#### Respuestas
- **`200 OK`**: Objeto paginado (`count`, `next`, `previous`, `results`):
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id_cochera": 10,
      "nombre": "Cochera Los Pinos",
      "direccion": "Av. Insurgentes Sur 1450, Col. Actipan, C.P. 03230",
      "latitud": "19.37124500",
      "longitud": "-99.17834200",
      "metros_cuadrados": "35.50",
      "espacios_totales": 2,
      "tarifa_hora": "45.00",
      "tipo_vehiculo": "AMBOS",
      "estado_cochera": "PENDIENTE",
      "foto_principal": "https://storage.parkshare.mx/cocheras/pinos_1.jpg",
      "arrendador": {
        "id_usuario": 2,
        "nombre_completo": "Carlos Eduardo Ramírez Pérez",
        "correo": "carlos.ramirez@gmail.com",
        "telefono": "5598765432"
      },
      "fotos": [
        { "id_foto": 1, "url_foto": "https://storage.parkshare.mx/cocheras/pinos_1.jpg" },
        { "id_foto": 2, "url_foto": "https://storage.parkshare.mx/cocheras/pinos_2.webp" }
      ],
      "disponibilidades": [
        { "id_disponibilidad": 1, "dia_semana": "L", "hora_inicio": "07:00:00", "hora_fin": "22:00:00" },
        { "id_disponibilidad": 2, "dia_semana": "M", "hora_inicio": "07:00:00", "hora_fin": "22:00:00" }
      ]
    }
  ]
}
```
- **`400 Bad Request`**: Parámetro `estado` inválido.
- **`401 Unauthorized`**: Sin token válido.
- **`403 Forbidden`**: Usuario con rol distinto de `ADMIN`.

---

### 5.7 `PATCH /admin/cocheras/{id}/validar`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsAdminRole`).
- **Descripción**: El administrador aprueba (`APROBADA`) o rechaza (`RECHAZADA`) una cochera en revisión (`PENDIENTE`) conforme a **RN-10**.

#### Request Payload
```json
{
  "estado_cochera": "APROBADA"
}
```
*(O en caso de rechazo: `{ "estado_cochera": "RECHAZADA", "motivo": "Las fotografías no permiten validar condiciones seguras para vehículos." }`)*

#### Respuestas
- **`200 OK`**:
```json
{
  "mensaje": "Cochera aprobada y publicada exitosamente.",
  "id_cochera": 10,
  "estado_cochera": "APROBADA",
  "motivo": null
}
```
- **`400 Bad Request`**: `estado_cochera` distinto de `APROBADA` o `RECHAZADA`, o rechazo sin `motivo` no vacío.
- **`403 Forbidden`**: Usuario no es `ADMIN`.
- **`404 Not Found`**: Cochera inexistente.
- **`409 Conflict`**: La cochera no está en estado `PENDIENTE`.

---

## 6. Escenarios de Prueba para TDD

### 6.1 Casos Felices (Happy Path)
1. **Registro completo de cochera (`CU-02`, `CU-03`, `RN-08`, `RN-09`)**: Arrendador con cuenta `ACTIVA` crea cochera con 2 fotos válidas (`.jpg`, `.webp`), `metros_cuadrados=35.50`, `espacios_totales=2`, `tarifa_hora=45.00` y franjas de lunes y martes -> `201 Created` y `estado_cochera='PENDIENTE'`.
2. **Validación por administrador (`RN-10`)**: `ADMIN` aprueba la cochera vía `PATCH /admin/cocheras/{id}/validar` -> `estado_cochera='APROBADA'`.
3. **Búsqueda por cercanía (`CU-04`)**: Arrendatario consulta `GET /cocheras?lat=19.37&lng=-99.18` con varias cocheras `APROBADA`, `PENDIENTE`, `RECHAZADA` e `INACTIVA`; solo aparecen las `APROBADA`, ordenadas de menor a mayor `distancia_km`.
4. **Búsqueda ordenada por `precio` y `disponibilidad`**: `orden=precio` ordena por `tarifa_hora` ascendente; `orden=disponibilidad` prioriza cocheras abiertas en la hora actual con mayor `espacios_disponibles_ahora`.
5. **Listado propio del arrendador**: `GET /cocheras/mis-cocheras` devuelve todas sus cocheras (`PENDIENTE`, `APROBADA`, `INACTIVA`, `RECHAZADA`) y oculta las de otros arrendadores.
6. **Pausa y reactivación temporal por propietario**: Arrendador dueño cambia su cochera `APROBADA -> INACTIVA` y luego `INACTIVA -> APROBADA` mediante `PATCH /cocheras/{id}` (`200 OK`).
7. **Actualización de tarifa y horario (`CU-03`)**: Arrendador actualiza `tarifa_hora` y reemplaza franjas en `disponibilidades` (`200 OK`).
8. **Listado administrativo de cocheras (`GET /admin/cocheras`, `RN-10`)**: `ADMIN` consulta `GET /admin/cocheras` y recibe por defecto solo las cocheras `PENDIENTE` ordenadas por `-id_cochera`, paginadas (`count`, `next`, `previous`, `results`), con datos de `arrendador` (`id_usuario`, `nombre_completo`, `correo`, `telefono`), `foto_principal`, `fotos` y `disponibilidades`.
9. **Filtros administrativos en `GET /admin/cocheras`**: Con `?estado=APROBADA` devuelve solo cocheras aprobadas; con `?estado=TODOS` devuelve cocheras en cualquier estado (`PENDIENTE`, `APROBADA`, `RECHAZADA`, `INACTIVA`).

### 6.2 Casos de Error y Validación
1. **Bloqueo por `RN-08`**: `ARRENDADOR` en estado `PENDIENTE` o `RECHAZADA`, o usuario con rol `ARRENDATARIO`, intenta `POST /cocheras` -> `403 Forbidden`.
2. **Validaciones de `RN-09`**:
   - Sin fotos (`fotos: []`) -> `400 Bad Request`.
   - Foto con extensión no permitida (`.pdf` o `.gif`) -> `400 Bad Request`.
   - `metros_cuadrados <= 0` -> `400 Bad Request`.
   - `espacios_totales = 0` o negativo -> `400 Bad Request`.
   - `tarifa_hora <= 0` -> `400 Bad Request`.
3. **Validaciones de `CU-03` (`disponibilidad`)**:
   - Lista `disponibilidades` vacía -> `400 Bad Request`.
   - `hora_fin <= hora_inicio` (ej. `18:00` a `09:00`) -> `400 Bad Request`.
   - Dos franjas superpuestas en el mismo `dia_semana` (ej. `08:00-14:00` y `12:00-18:00` en `'L'`) -> `400 Bad Request`.
4. **Coordenadas inválidas**: `latitud = 95.0` o `longitud = -190.0` en `POST /cocheras` o `GET /cocheras` -> `400 Bad Request`.
5. **Seguridad de propiedad**: Arrendador B intenta `PATCH /cocheras/{id}` de una cochera de Arrendador A -> `403 Forbidden`.
6. **Intento de auto-aprobación (`RN-10`)**: Arrendador intenta `PATCH /cocheras/{id}` con `estado_cochera: "APROBADA"` cuando la cochera está `PENDIENTE` o `RECHAZADA` -> `403 Forbidden` / `409 Conflict`.
7. **Acceso no-admin a validación**: Arrendador o Arrendatario intenta `PATCH /admin/cocheras/{id}/validar` -> `403 Forbidden`.
8. **Validaciones y seguridad en `GET /admin/cocheras`**:
   - Parámetro `estado` inválido (ej. `?estado=BORRADA`) -> `400 Bad Request`.
   - Petición sin token válido -> `401 Unauthorized`.
   - Petición con usuario cuyo rol es `ARRENDADOR` o `ARRENDATARIO` -> `403 Forbidden`.

### 6.3 Casos Límite (Edge Cases)
1. **Fuerza de estado inicial `PENDIENTE`**: Si el arrendador envía `"estado_cochera": "APROBADA"` en el payload de `POST /cocheras`, el sistema lo ignora o rechaza y la cochera nunca nace `APROBADA`.
2. **Coordenadas idénticas en Haversine**: `GET /cocheras?lat=19.37124500&lng=-99.17834200` (misma ubicación exacta que la cochera) calcula `distancia_km = 0.0` sin error de dominio matemático en `ACOS`.
3. **Dos franjas contiguas no traslapadas en el mismo día**: `08:00-12:00` y `13:00-18:00` (o `08:00-12:00` y `12:00-18:00`) en `'L'` son válidas y aceptadas (`201 Created`).
4. **Visibilidad de cochera no aprobada en `GET /cocheras/{id}`**: Arrendatario solicitando detalle de una cochera `PENDIENTE` o `INACTIVA` recibe `404 Not Found`; su dueño o el `ADMIN` reciben `200 OK`.
5. **Doble validación administrativa**: Intentar validar una cochera que ya está `APROBADA` o `RECHAZADA` devuelve `409 Conflict`.
6. **Filtro de `tipo_vehiculo`**: Buscar con `?tipo_vehiculo=MOTO` devuelve cocheras `MOTO` y `AMBOS`, excluyendo `AUTO`.
7. **Optimización ORM**: `GET /cocheras`, `GET /cocheras/{id}` y `GET /admin/cocheras` usan `select_related('arrendador')` y `prefetch_related('fotos', 'disponibilidades')` manteniendo número constante de queries (`assertNumQueries`, sin consultas N+1).
8. **Paginación en `GET /admin/cocheras`**: Soporta `?page=` y `?page_size=` devolviendo los metadatos `count`, `next`, `previous` y `results` ordenados por `-id_cochera`.
