# Constitución — ParkShareApp
Principios innegociables. Toda spec, plan y tarea debe cumplirlos.
1. **Respetar Arquitectura**: En esta app la intrefaz se esta desarrollando en React Native, los datos van hacer llamados a traves de la llamada a la API del proyecto ParkShare_Backend y la API de Google Maps para mostrar el mapa en la interfaz. 

2. **La spec manda**: nada se implementa si no está en la spec activa. Si falta una
decisión, se para y se pregunta.

3. **Tests como puerta**: Prohibido avanzar con test fallidos, al fallar parate a preguntar y sugiere una posible solución.

4. **Idioma**: código en inglés; interfaz y documentación en español.

5. **Lógica de negocio**: En este proyecto solo se va desarrollar el back de la aplicación , por lo tanto esta app solo se va encargar de consultar los datos a la base de datos respetando la arquitectura definida en este proyecto. 

## Reglas que debes consultar al implementar la lógica de negocio.

### 11. Reglas y restricciones de negocio

#### 11.1 Al reservar una cochera (arrendatario)

| ID | Regla | Descripción |
| :--- | :--- | :--- |
| **RN-01** | Duración de la reservación | Al reservar, el arrendatario debe elegir uno de los tiempos que muestra la aplicación: mínimo de 30 minutos y máximo de 24 horas, en intervalos de 30 minutos (30 min, 1 h, 1:30 h, 2 h, etc.). |
| **RN-02** | Tiempo de tolerancia | Cuando termina el tiempo reservado, la aplicación otorga 10 minutos de tolerancia para que el arrendatario retire su vehículo sin cargo adicional. |
| **RN-03** | Recargo por retraso | Si el vehículo no se retira al terminar la tolerancia, se cobran $10 MXN por cada minuto de retraso. Ejemplo: 10 minutos tarde equivalen a un cargo de $100 MXN. |
| **RN-04** | Cancelación sin costo | El arrendatario puede cancelar su reservación sin penalización durante los primeros 10 minutos posteriores a haberla realizado. |
| **RN-05** | Multa por cancelación tardía | Pasado ese plazo de 10 minutos, el arrendatario aún puede cancelar, pero se le cobra una multa de $100 MXN. |
| **RN-06** | QR de salida | Al hacer su salida, el arrendatario debe mostrar su código QR de salida. |

#### 11.2 Al publicar una cochera (arrendador)

| ID | Regla | Descripción |
| :--- | :--- | :--- |
| **RN-07** | Registro del arrendador | Para ser arrendador, el usuario debe registrarse y proporcionar su nombre completo, correo electrónico (Gmail), teléfono, fecha de nacimiento, INE, constancia de situación fiscal y los documentos que validen que es dueño de la propiedad. |
| **RN-08** | Alta por el administrador | La cuenta del arrendador solo queda activa cuando el administrador verifica sus datos y documentos (CU-10). |
| **RN-09** | Datos de la cochera | Al registrar una cochera, el arrendador debe cargar las fotos del espacio, indicar su superficie en metros cuadrados y la cantidad de espacios disponibles para estacionarse. |
| **RN-10** | Validación de la publicación | El administrador valida cada publicación y solo se publica si cumple las condiciones necesarias para resguardar vehículos, como autos o motos. |

