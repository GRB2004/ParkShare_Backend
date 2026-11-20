# Especificación Técnica — Módulo 01: Autenticación, Usuarios y Administración de Cuentas (`01-auth`)

---

## 1. Resumen y Alcance

Este módulo define la base de identidad, autenticación, autorización basada en roles y gobierno administrativo de cuentas de **ParkShare_Backend** (Django + Django REST Framework + MySQL).

### Alcance incluido
- Registro de usuarios diferenciando perfiles de **`ARRENDATARIO`** y **`ARRENDADOR`** (con carga y validación de documentos legales requeridos).
- Autenticación por correo electrónico y contraseña mediante tokens JWT (`Bearer`), con validación estricta del estado de cuenta.
- Flujo administrativo de revisión, aprobación o rechazo de solicitudes de alta de arrendadores (**CU-10**).
- Flujo administrativo de búsqueda de cuentas, consulta de perfil con resumen disciplinario y suspensión inmediata con invalidación de sesiones activas (**CU-11**).
- Permisos reutilizables por rol (`ARRENDATARIO`, `ARRENDADOR`, `ADMIN`) y estado de cuenta (`ACTIVA`) para proteger todos los módulos del sistema.

### Fuera de alcance en este módulo (puntos de integración)
- La creación y gestión de las tablas `queja` y `sancion` pertenecen a módulos posteriores; sin embargo, el contrato de `GET /admin/cuentas/{id}` expone desde ahora los campos `resumen_disciplinario`, `historial_quejas` e `historial_sanciones` (retornando arreglos vacíos y conteos en `0` hasta que dichas tablas existan, además del `historial_suspensiones` real de `suspension_cuenta`).
- La recuperación de contraseña por correo o notificaciones push quedan fuera del alcance de esta especificación.

> **Nota técnica de enrutamiento (`ParkShare_Backend/urls.py`)**:
> Actualmente `ParkShare_Backend/urls.py` monta el panel administrativo nativo de Django en `path('admin/', admin.site.urls)`. Antes de implementar los endpoints `/admin/solicitudes`, `/admin/cuentas` (Módulo 01) y `/admin/cocheras/...` (Módulo 02), el equipo debe confirmar si:
> - **Opción A (Recomendada):** Toda la API REST se monta bajo el prefijo `/api/v1/` (ej. `/api/v1/auth/login`, `/api/v1/admin/solicitudes`), manteniendo el admin nativo de Django en `/admin/`.
> - **Opción B:** Se mantienen rutas raíz literales (`/auth/login`, `/admin/solicitudes`) reubicando el admin nativo de Django a `/django-admin/`.
> Los contratos de este documento usan las rutas base indicadas en el diseño funcional; el prefijo definitivo deberá quedar fijado antes de codificar las URLs.

---

## 2. Casos de Uso Cubiertos (CIM)

| ID | Nombre | Actor | Descripción |
| :--- | :--- | :--- | :--- |
| **CU-01** | Iniciar sesión | Arrendatario / Arrendador / Admin | Autenticación previa a cualquier operación del sistema. Valida credenciales y estado de cuenta (`ACTIVA`, `PENDIENTE`, `RECHAZADA`, `SUSPENDIDA`), emitiendo tokens de acceso para operar según rol. Todos los demás casos de uso protegidos requieren haber pasado por CU-01. |
| **CU-10** | Alta a arrendador | Administrador | El administrador consulta la lista de solicitudes de arrendadores en estado `PENDIENTE`, revisa sus datos personales y los 3 documentos adjuntos (INE, Constancia de Situación Fiscal, Comprobante de Propiedad), y decide **aprobar** el alta (activando la cuenta) o **rechazarla** indicando un motivo catalogado (`INE vencida`, `Constancia inactiva`, `Datos no coinciden`, `Otro` + detalle). |
| **CU-11** | Suspender cuenta de usuario | Administrador | El administrador busca cuentas por nombre completo o correo, consulta el perfil detallado junto con su historial de quejas, sanciones y suspensiones, y puede suspender la cuenta registrando un motivo obligatorio. La suspensión invalida sesiones/tokens activos y bloquea cualquier acceso posterior. |

---

## 3. Reglas de Negocio Aplicables (`RN-XX`)

| Regla | Aplicación concreta en el módulo |
| :--- | :--- |
| **RN-07** (Registro del arrendador y perfil inicial) | Para registrarse como `ARRENDADOR`, es obligatorio proporcionar: `nombre_completo` (solo caracteres alfabéticos y espacios), `correo` con dominio **`@gmail.com`** obligatorio, `telefono` numérico válido (10 dígitos / E.164), `password` segura, `fecha_nacimiento` (mayor de 18 años), y los 3 documentos en `documento_arrendador`: `url_ine` (PDF o imagen `.jpg/.jpeg/.png`), `url_csf` (PDF del SAT) y `url_propiedad` (PDF legal del inmueble). Para `ARRENDATARIO`, son obligatorios: `nombre_completo`, `correo` (formato email válido), `telefono`, `password`, `fecha_nacimiento` (>18 años), `placas` y `modelo_vehiculo`. |
| **RN-08** (Alta por el administrador) | Todo usuario con rol `ARRENDATARIO` nace con `estado_cuenta = 'ACTIVA'`. Todo usuario con rol `ARRENDADOR` nace con `estado_cuenta = 'PENDIENTE'` y su registro en `documento_arrendador` nace con `estado_verificacion = 'PENDIENTE'`. Su cuenta **solo pasa a `'ACTIVA'`** cuando un `ADMIN` aprueba su solicitud en **CU-10**. Mientras esté `'PENDIENTE'` o `'RECHAZADA'`, el usuario tiene bloqueada toda operación operativa (publicar/editar cocheras, operar reservas, etc.) con código HTTP `403`. |

### Políticas de Seguridad y Estados de Cuenta
1. **Hash de contraseñas**: Todas las contraseñas se almacenan con PBKDF2-SHA256 (nativo de Django) o Argon2 en `password_hash`; nunca se almacenan ni devuelven en texto plano.
2. **Mayoría de edad**: `fecha_nacimiento` se evalúa contra la fecha actual en zona horaria `America/Mexico_City`. Un usuario debe tener **18 años cumplidos o más** al día del registro (no se permite un día menos)**.
3. **Rol en registro público**: `POST /auth/registro` solo permite registrar cuentas con rol `ARRENDATARIO` o `ARRENDADOR`. Cualquier intento de enviar `rol = 'ADMIN'` debe rechazarse con `400 Bad Request` o `403 Forbidden` (regla de seguridad: las cuentas `ADMIN` solo se crean por administración interna/CLI).
4. **Control de cuenta `SUSPENDIDA` (CU-11)**:
   - Al suspender una cuenta, `usuario.estado_cuenta` pasa a `'SUSPENDIDA'`, se incrementa `usuario.token_version` (invalidando inmediatamente todo JWT previamente emitido), se revocan refresh tokens activos si aplica y se crea un registro en `suspension_cuenta` con `estado = 'ACTIVA'`.
   - En `POST /auth/login`, cualquier cuenta en estado `'SUSPENDIDA'` es rechazada con **`403 Forbidden`**.
   - Cualquier petición autenticada con un token anterior de una cuenta suspendida (o con `token_version` desactualizada) es rechazada con **`401 Unauthorized` / `403 Forbidden`**.
5. **Comportamiento en `login` para `PENDIENTE` y `RECHAZADA`**:
   - Para que el arrendador pueda consultar el estado de su solicitud (o el `motivo_rechazo` de `CU-10`) en la app móvil, `POST /auth/login` valida credenciales y devuelve `200 OK` con el perfil incluyendo `estado_cuenta` (`PENDIENTE` o `RECHAZADA`) y `motivo_rechazo` (cuando aplique), mientras que el permiso operativo `IsActiveAccount` bloquea con **`403 Forbidden`** toda operación de negocio hasta que la cuenta pase a `ACTIVA` (`RN-08`).

---

## 4. Modelo de Datos (Diccionario de Datos / PSM)

### 4.1 Tabla `usuario`
Modelo de usuario personalizado (`AUTH_USER_MODEL = 'ParkShare_Backend.Usuario'`, heredando de `AbstractBaseUser` y `PermissionsMixin`).

| Columna (`db_column`) | Tipo SQL / MySQL | Nulidad | Restricciones / Default | Descripción |
| :--- | :--- | :--- | :--- | :--- |
| `id_usuario` | `BIGINT AUTO_INCREMENT` | NO NULO | `PK` | Identificador único del usuario. |
| `nombre_completo` | `VARCHAR(150)` | NO NULO | Formato alfabético (`^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s]+$`) | Nombre completo del usuario. |
| `correo` | `VARCHAR(120)` | NO NULO | `UNIQUE`, `INDEX`, formato email (`@gmail.com` si `rol = 'ARRENDADOR'`) | Correo electrónico de acceso (normalizado a minúsculas). |
| `telefono` | `VARCHAR(15)` | NO NULO | Numérico 10 dígitos (`^\d{10}$`) o E.164 (`^\+[1-9]\d{9,14}$`) | Teléfono de contacto. |
| `password_hash` | `VARCHAR(255)` | NO NULO | PBKDF2 / Argon2 | Hash criptográfico de la contraseña (`password` en ORM mapeado a `db_column='password_hash'`). |
| `fecha_nacimiento` | `DATE` | NO NULO | Edad `>= 18` años | Fecha de nacimiento. |
| `rol` | `ENUM('ARRENDATARIO', 'ARRENDADOR', 'ADMIN')` | NO NULO | Default `'ARRENDATARIO'`, `INDEX` | Rol funcional dentro de ParkShare. |
| `estado_cuenta` | `ENUM('ACTIVA', 'PENDIENTE', 'RECHAZADA', 'SUSPENDIDA')` | NO NULO | `INDEX` (Arrendatario: `'ACTIVA'`; Arrendador: `'PENDIENTE'`) | Estado administrativo de la cuenta. |
| `placas` | `VARCHAR(20)` | SÍ NULO | Requerido si `rol = 'ARRENDATARIO'` | Placas del vehículo del arrendatario. |
| `modelo_vehiculo` | `VARCHAR(100)` | SÍ NULO | Requerido si `rol = 'ARRENDATARIO'` | Marca/modelo del vehículo del arrendatario. |
| `cuenta_cobro` | `VARCHAR(100)` | SÍ NULO | Solo aplicable a `rol = 'ARRENDADOR'` | CLABE / cuenta bancaria de cobro del arrendador. |
| `token_version` | `INT` | NO NULO | Default `1` | Campo técnico de control de sesión; se incrementa en `CU-11` para invalidar tokens previos. |
| `is_staff` | `BOOLEAN` | NO NULO | Default `False` (`True` para `ADMIN`) | Requerido por `AbstractBaseUser`/Django Admin. |
| `is_active` | `BOOLEAN` | NO NULO | Default `True` (`False` si `SUSPENDIDA`) | Bandera de actividad técnica de Django Auth. |
| `last_login` | `DATETIME` | SÍ NULO | Default `NULL` | Último acceso registrado por Django Auth. |

> **Índices recomendados**: `correo` (único), `(rol, estado_cuenta)`, `nombre_completo`.

### 4.2 Tabla `documento_arrendador`

| Columna (`db_column`) | Tipo SQL / MySQL | Nulidad | Restricciones / Default | Descripción |
| :--- | :--- | :--- | :--- | :--- |
| `id_documento` | `BIGINT AUTO_INCREMENT` | NO NULO | `PK` | Identificador único del expediente documental. |
| `arrendador_id` | `BIGINT` | NO NULO | `FK -> usuario.id_usuario` (`ON DELETE CASCADE`), `INDEX` | Arrendador propietario de los documentos. |
| `url_ine` | `VARCHAR(255)` | NO NULO | Extensión válida `.pdf`, `.jpg`, `.jpeg`, `.png` | Ruta o URL de identificación oficial (INE). |
| `url_csf` | `VARCHAR(255)` | NO NULO | Extensión válida `.pdf` | Ruta o URL de Constancia de Situación Fiscal (SAT). |
| `url_propiedad` | `VARCHAR(255)` | NO NULO | Extensión válida `.pdf` | Ruta o URL de documento que acredita propiedad legal. |
| `estado_verificacion` | `ENUM('PENDIENTE', 'APROBADO', 'RECHAZADO')` | NO NULO | Default `'PENDIENTE'`, `INDEX` | Resultado de revisión administrativa (`CU-10`). |
| `motivo_rechazo` | `TEXT` | SÍ NULO | Obligatorio cuando `estado_verificacion = 'RECHAZADO'` | Motivo estructurado de rechazo indicado por el admin. |

### 4.3 Tabla `suspension_cuenta`

| Columna (`db_column`) | Tipo SQL / MySQL | Nulidad | Restricciones / Default | Descripción |
| :--- | :--- | :--- | :--- | :--- |
| `id_suspension` | `BIGINT AUTO_INCREMENT` | NO NULO | `PK` | Identificador único del evento de suspensión. |
| `usuario_id` | `BIGINT` | NO NULO | `FK -> usuario.id_usuario` (`ON DELETE CASCADE`), `INDEX` | Usuario cuya cuenta fue suspendida. |
| `admin_id` | `BIGINT` | NO NULO | `FK -> usuario.id_usuario` (`ON DELETE RESTRICT`), `INDEX` | Administrador que ejecutó la sanción. |
| `motivo` | `TEXT` | NO NULO | No vacío | Justificación obligatoria de la suspensión. |
| `estado` | `ENUM('ACTIVA', 'REVOCADA')` | NO NULO | Default `'ACTIVA'` | Estado de la suspensión. |
| `fecha_suspension` | `DATETIME` | NO NULO | `auto_now_add=True` | Marca temporal exacta de la suspensión. |

### 4.4 Diagrama ER Simplificado
```
usuario (1) ──────< (N) documento_arrendador
usuario (1) ──────< (N) suspension_cuenta [usuario_id]
usuario (1) ──────< (N) suspension_cuenta [admin_id]
```

---

## 5. Contratos de API REST (Endpoints)

> Todos los errores siguen un formato uniforme en español con `detail` y `errors` (cuando hay validaciones por campo).

### 5.1 `POST /auth/registro`
- **Autenticación y Permisos**: Público (`AllowAny`).
- **Descripción**: Registra un nuevo usuario `ARRENDATARIO` (`estado_cuenta = 'ACTIVA'`) o `ARRENDADOR` (`estado_cuenta = 'PENDIENTE'` + expediente en `documento_arrendador`).

#### Request Payload — Caso `ARRENDATARIO` (`application/json`)
```json
{
  "nombre_completo": "Ana Sofía Martínez López",
  "correo": "ana.martinez@outlook.com",
  "telefono": "5512345678",
  "password": "MiPasswordSegura123!",
  "fecha_nacimiento": "1998-05-14",
  "rol": "ARRENDATARIO",
  "placas": "ABC-123-D",
  "modelo_vehiculo": "Nissan Versa 2022"
}
```

#### Request Payload — Caso `ARRENDADOR` (`application/json`)
```json
{
  "nombre_completo": "Carlos Eduardo Ramírez Pérez",
  "correo": "carlos.ramirez@gmail.com",
  "telefono": "5598765432",
  "password": "MiPasswordSegura123!",
  "fecha_nacimiento": "1985-11-20",
  "rol": "ARRENDADOR",
  "cuenta_cobro": "012180001234567890",
  "url_ine": "https://storage.parkshare.mx/docs/carlos_ine.jpg",
  "url_csf": "https://storage.parkshare.mx/docs/carlos_csf.pdf",
  "url_propiedad": "https://storage.parkshare.mx/docs/carlos_escritura.pdf"
}
```
*(Nota: Si se utiliza `multipart/form-data`, el backend puede aceptar archivos en `ine`, `csf`, `propiedad`, validar extensión/tamaño, almacenarlos y guardar la ruta resultante `<= 255` chars en `url_ine`, `url_csf`, `url_propiedad`.)*

#### Respuestas
- **`201 Created`** (`ARRENDATARIO`):
```json
{
  "mensaje": "Cuenta de arrendatario creada exitosamente.",
  "usuario": {
    "id_usuario": 1,
    "nombre_completo": "Ana Sofía Martínez López",
    "correo": "ana.martinez@outlook.com",
    "telefono": "5512345678",
    "fecha_nacimiento": "1998-05-14",
    "rol": "ARRENDATARIO",
    "estado_cuenta": "ACTIVA",
    "placas": "ABC-123-D",
    "modelo_vehiculo": "Nissan Versa 2022"
  }
}
```
- **`201 Created`** (`ARRENDADOR`):
```json
{
  "mensaje": "Solicitud de arrendador registrada. Tu cuenta quedará activa tras la verificación del administrador.",
  "usuario": {
    "id_usuario": 2,
    "nombre_completo": "Carlos Eduardo Ramírez Pérez",
    "correo": "carlos.ramirez@gmail.com",
    "telefono": "5598765432",
    "fecha_nacimiento": "1985-11-20",
    "rol": "ARRENDADOR",
    "estado_cuenta": "PENDIENTE",
    "cuenta_cobro": "012180001234567890"
  },
  "documento": {
    "id_documento": 1,
    "estado_verificacion": "PENDIENTE",
    "url_ine": "https://storage.parkshare.mx/docs/carlos_ine.jpg",
    "url_csf": "https://storage.parkshare.mx/docs/carlos_csf.pdf",
    "url_propiedad": "https://storage.parkshare.mx/docs/carlos_escritura.pdf"
  }
}
```
- **`400 Bad Request`**: Correo duplicado, menor de 18 años, `nombre_completo` con números/símbolos, correo no `@gmail.com` en arrendador, falta de `placas`/`modelo_vehiculo` en arrendatario, falta o extensión inválida en documentos de arrendador, contraseña débil o intento de registrarse como `ADMIN`.

---

### 5.2 `POST /auth/login`
- **Autenticación y Permisos**: Público (`AllowAny`).
- **Descripción**: Autentica al usuario con `correo` y `password`, devuelve tokens JWT (`access`, `refresh`) y datos de perfil.

#### Request Payload
```json
{
  "correo": "carlos.ramirez@gmail.com",
  "password": "MiPasswordSegura123!"
}
```

#### Respuestas
- **`200 OK`**:
```json
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "usuario": {
    "id_usuario": 2,
    "nombre_completo": "Carlos Eduardo Ramírez Pérez",
    "correo": "carlos.ramirez@gmail.com",
    "telefono": "5598765432",
    "rol": "ARRENDADOR",
    "estado_cuenta": "ACTIVA",
    "motivo_rechazo": null
  }
}
```
*(Si `estado_cuenta` es `RECHAZADA`, `motivo_rechazo` incluye el motivo registrado en `documento_arrendador` para informarlo en pantalla, manteniendo bloqueadas las operaciones protegidas por `IsActiveAccount`.)*
- **`401 Unauthorized`**: Credenciales incorrectas (`correo` inexistente o `password` errónea):
```json
{
  "detail": "Correo o contraseña incorrectos."
}
```
- **`403 Forbidden`**: Cuenta en estado `SUSPENDIDA`:
```json
{
  "detail": "Tu cuenta ha sido suspendida. Motivo: Uso indebido reiterado.",
  "codigo": "CUENTA_SUSPENDIDA",
  "estado_cuenta": "SUSPENDIDA"
}
```

---

### 5.3 `GET /admin/solicitudes`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsAdminRole`).
- **Query Params**:
  - `estado` (opcional, default `PENDIENTE`): `PENDIENTE` | `APROBADO` | `RECHAZADO` | `TODOS`.
  - `page`, `page_size` (paginación estándar).
- **Respuestas**:
  - **`200 OK`**:
```json
{
  "count": 1,
  "results": [
    {
      "id_documento": 1,
      "estado_verificacion": "PENDIENTE",
      "arrendador": {
        "id_usuario": 2,
        "nombre_completo": "Carlos Eduardo Ramírez Pérez",
        "correo": "carlos.ramirez@gmail.com",
        "telefono": "5598765432",
        "fecha_nacimiento": "1985-11-20",
        "estado_cuenta": "PENDIENTE"
      }
    }
  ]
}
```
  - **`401 Unauthorized`**: Sin token válido.
  - **`403 Forbidden`**: Usuario autenticado con rol `ARRENDATARIO` o `ARRENDADOR`.

---

### 5.4 `GET /admin/solicitudes/{id}`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsAdminRole`).
- **Path Param**: `id` corresponde a `id_documento`.
- **Respuestas**:
  - **`200 OK`**:
```json
{
  "id_documento": 1,
  "estado_verificacion": "PENDIENTE",
  "motivo_rechazo": null,
  "url_ine": "https://storage.parkshare.mx/docs/carlos_ine.jpg",
  "url_csf": "https://storage.parkshare.mx/docs/carlos_csf.pdf",
  "url_propiedad": "https://storage.parkshare.mx/docs/carlos_escritura.pdf",
  "arrendador": {
    "id_usuario": 2,
    "nombre_completo": "Carlos Eduardo Ramírez Pérez",
    "correo": "carlos.ramirez@gmail.com",
    "telefono": "5598765432",
    "fecha_nacimiento": "1985-11-20",
    "cuenta_cobro": "012180001234567890",
    "estado_cuenta": "PENDIENTE"
  }
}
```
  - **`404 Not Found`**: No existe solicitud con ese `id_documento`.
  - **`403 Forbidden`**: Rol distinto de `ADMIN`.

---

### 5.5 `PATCH /admin/solicitudes/{id}`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsAdminRole`).
- **Descripción**: Resuelve una solicitud en estado `PENDIENTE` (**CU-10** / **RN-08**).
  - Si `estado_verificacion = "APROBADO"`: actualiza `documento_arrendador.estado_verificacion = 'APROBADO'`, `motivo_rechazo = NULL` y `usuario.estado_cuenta = 'ACTIVA'`.
  - Si `estado_verificacion = "RECHAZADO"`: exige `motivo_rechazo` perteneciente al catálogo (`"INE vencida"`, `"Constancia inactiva"`, `"Datos no coinciden"`, `"Otro"`); si es `"Otro"`, exige `detalle_motivo` no vacío. Actualiza `documento_arrendador.estado_verificacion = 'RECHAZADO'`, guarda el motivo completo en `motivo_rechazo` y actualiza `usuario.estado_cuenta = 'RECHAZADA'`.

#### Request Payload — Aprobación
```json
{
  "estado_verificacion": "APROBADO"
}
```

#### Request Payload — Rechazo
```json
{
  "estado_verificacion": "RECHAZADO",
  "motivo_rechazo": "Otro",
  "detalle_motivo": "El nombre en la constancia fiscal no coincide con el de la INE."
}
```

#### Respuestas
- **`200 OK`**:
```json
{
  "mensaje": "Solicitud aprobada. La cuenta del arrendador ha sido activada.",
  "id_documento": 1,
  "estado_verificacion": "APROBADO",
  "motivo_rechazo": null,
  "arrendador_estado_cuenta": "ACTIVA"
}
```
- **`400 Bad Request`**: `estado_verificacion` inválido, rechazo sin `motivo_rechazo`, o `motivo_rechazo = "Otro"` sin `detalle_motivo`.
- **`404 Not Found`**: Solicitud inexistente.
- **`409 Conflict`**: La solicitud ya fue resuelta anteriormente (`estado_verificacion != 'PENDIENTE'`).

---

### 5.6 `GET /admin/cuentas?q=`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsAdminRole`).
- **Query Params**:
  - `q` (opcional): texto de búsqueda sobre `nombre_completo` o `correo` (insensible a mayúsculas).
  - `rol` (opcional): `ARRENDATARIO` | `ARRENDADOR` | `ADMIN`.
  - `estado_cuenta` (opcional): `ACTIVA` | `PENDIENTE` | `RECHAZADA` | `SUSPENDIDA`.
- **Respuestas**:
  - **`200 OK`**:
```json
{
  "count": 2,
  "results": [
    {
      "id_usuario": 1,
      "nombre_completo": "Ana Sofía Martínez López",
      "correo": "ana.martinez@outlook.com",
      "telefono": "5512345678",
      "rol": "ARRENDATARIO",
      "estado_cuenta": "ACTIVA"
    }
  ]
}
```

---

### 5.7 `GET /admin/cuentas/{id}`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsAdminRole`).
- **Descripción**: Consulta detallada del perfil de usuario e historial disciplinario (**CU-11**).
- **Respuestas**:
  - **`200 OK`**:
```json
{
  "id_usuario": 1,
  "nombre_completo": "Ana Sofía Martínez López",
  "correo": "ana.martinez@outlook.com",
  "telefono": "5512345678",
  "fecha_nacimiento": "1998-05-14",
  "rol": "ARRENDATARIO",
  "estado_cuenta": "ACTIVA",
  "placas": "ABC-123-D",
  "modelo_vehiculo": "Nissan Versa 2022",
  "cuenta_cobro": null,
  "resumen_disciplinario": {
    "total_quejas": 0,
    "total_sanciones": 0,
    "total_suspensiones": 0
  },
  "historial_quejas": [],
  "historial_sanciones": [],
  "historial_suspensiones": []
}
```
  - **`404 Not Found`**: Usuario no encontrado.

---

### 5.8 `POST /admin/cuentas/{id}/suspender`
- **Autenticación y Permisos**: JWT requerido (`IsAuthenticated`, `IsAdminRole`).
- **Descripción**: Suspende la cuenta del usuario objetivo, registra el evento en `suspension_cuenta`, incrementa `token_version` e invalida sus sesiones activas (**CU-11**).

#### Request Payload
```json
{
  "motivo": "Reincidencia en daños reportados por arrendadores."
}
```

#### Respuestas
- **`200 OK`**:
```json
{
  "mensaje": "Cuenta suspendida exitosamente.",
  "suspension": {
    "id_suspension": 1,
    "usuario_id": 1,
    "admin_id": 99,
    "motivo": "Reincidencia en daños reportados por arrendadores.",
    "estado": "ACTIVA",
    "fecha_suspension": "2026-11-19T15:30:00-06:00"
  },
  "estado_cuenta": "SUSPENDIDA"
}
```
- **`400 Bad Request`**: `motivo` vacío/ausente o intento del administrador de suspender su propia cuenta o a otro `ADMIN`.
- **`404 Not Found`**: Usuario objetivo inexistente.
- **`409 Conflict`**: La cuenta ya se encuentra en estado `SUSPENDIDA`.

---

## 6. Escenarios de Prueba para TDD

### 6.1 Casos Felices (Happy Path)
1. **Registro exitoso de `ARRENDATARIO`**: Crea usuario con `estado_cuenta = 'ACTIVA'`, `password_hash` hasheado (no texto plano), `placas` y `modelo_vehiculo` persistidos (`201 Created`).
2. **Registro exitoso de `ARRENDADOR` (`RN-07`, `RN-08`)**: Con correo `@gmail.com`, edad `>= 18` y los 3 documentos válidos (`url_ine`, `url_csf`, `url_propiedad`), crea usuario con `estado_cuenta = 'PENDIENTE'` y `documento_arrendador` con `estado_verificacion = 'PENDIENTE'` (`201 Created`).
3. **Login exitoso de cuenta `ACTIVA` (`CU-01`)**: Devuelve `200 OK`, tokens JWT válidos y datos del perfil.
4. **Listado y detalle de solicitudes pendientes por `ADMIN` (`CU-10`)**: `GET /admin/solicitudes` devuelve solo `PENDIENTE` por defecto; `GET /admin/solicitudes/{id}` incluye las 3 URLs documentales.
5. **Aprobación de arrendador por `ADMIN` (`CU-10`, `RN-08`)**: `PATCH /admin/solicitudes/{id}` con `APROBADO` cambia `estado_verificacion = 'APROBADO'` y `usuario.estado_cuenta = 'ACTIVA'`.
6. **Rechazo de arrendador por `ADMIN` (`CU-10`)**: `PATCH /admin/solicitudes/{id}` con `RECHAZADO` y motivo `"INE vencida"` o `"Otro"` + `detalle_motivo` cambia `estado_verificacion = 'RECHAZADO'`, `usuario.estado_cuenta = 'RECHAZADA'` y guarda `motivo_rechazo`.
7. **Búsqueda y consulta de cuenta por `ADMIN` (`CU-11`)**: `GET /admin/cuentas?q=ana` filtra por nombre o correo insensible a mayúsculas; `GET /admin/cuentas/{id}` devuelve perfil y resumen disciplinario.
8. **Suspensión de cuenta e invalidación de sesión (`CU-11`)**: `POST /admin/cuentas/{id}/suspender` cambia cuenta a `SUSPENDIDA`, crea `suspension_cuenta` en estado `ACTIVA`, incrementa `token_version` y provoca que: (a) tokens previos reciban `401/403`, y (b) `POST /auth/login` posterior retorne `403`.

### 6.2 Casos de Error y Validación
1. **Correo no Gmail en `ARRENDADOR` (`RN-07`)**: `POST /auth/registro` con `rol='ARRENDADOR'` y `correo='juan@hotmail.com'` retorna `400 Bad Request`.
2. **Menor de 18 años**: Usuario con 17 años y 364 días al momento del registro retorna `400 Bad Request`.
3. **Nombre con caracteres no alfabéticos**: `"Juan Pérez 123"` retorna `400 Bad Request`.
4. **Teléfono inválido**: Teléfono de 8 dígitos o con letras retorna `400 Bad Request`.
5. **Correo duplicado**: Registro con correo ya existente (sin importar mayúsculas/minúsculas) retorna `400 Bad Request`.
6. **Documentos faltantes o con extensión inválida en `ARRENDADOR`**: `url_csf` terminando en `.jpg` (debe ser `.pdf`) o falta de `url_propiedad` retorna `400 Bad Request`.
7. **Intento de autoasignación de rol `ADMIN` en registro**: `POST /auth/registro` con `"rol": "ADMIN"` retorna `400 Bad Request` / `403 Forbidden`.
8. **Falta de datos de vehículo en `ARRENDATARIO`**: Omisión de `placas` o `modelo_vehiculo` retorna `400 Bad Request`.
9. **Credenciales inválidas en `POST /auth/login`**: Contraseña incorrecta retorna `401 Unauthorized`.
10. **Control de acceso por rol**: `ARRENDATARIO` o `ARRENDADOR` intentando acceder a `/admin/solicitudes` o `/admin/cuentas/...` recibe `403 Forbidden`.
11. **Rechazo sin motivo o `Otro` sin detalle en `CU-10`**: `PATCH /admin/solicitudes/{id}` con `RECHAZADO` sin `motivo_rechazo` o con `"Otro"` sin `detalle_motivo` retorna `400 Bad Request`.
12. **Suspensión sin motivo o sobre cuenta `ADMIN` / propia**: Retorna `400 Bad Request`.

### 6.3 Casos Límite (Edge Cases)
1. **Cumpleaños exacto de 18 años hoy**: Usuario nacido exactamente hace 18 años en la fecha actual (`America/Mexico_City`) **sí** puede registrarse (`201 Created`).
2. **Normalización de correo**: `"Carlos.Ramirez@GMAIL.COM"` se normaliza a `"carlos.ramirez@gmail.com"`, pasa la validación `@gmail.com` y bloquea duplicados por variación de mayúsculas.
3. **Re-evaluación de solicitud ya resuelta**: Intentar `PATCH /admin/solicitudes/{id}` cuando `estado_verificacion` ya es `APROBADO` o `RECHAZADO` retorna `409 Conflict`.
4. **Doble suspensión**: Intentar suspender una cuenta ya `SUSPENDIDA` retorna `409 Conflict`.
5. **Arrendador `PENDIENTE` o `RECHAZADA` intenta operar (`RN-08`)**: Aunque valide su sesión para ver su estado, el permiso `IsActiveAccount` rechaza con `403 Forbidden` cualquier endpoint operativo.
6. **Prevención de N+1 en listados administrativos**: `GET /admin/solicitudes` usa `select_related('arrendador')` y `GET /admin/cuentas/{id}` usa `prefetch_related` en `suspension_cuenta`.
