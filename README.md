# Planning Tools v1.2.0 — Suite de Planeación

## Versión web

La aplicación publicada está en **https://planningtools.onrender.com/**.
El servicio de Render se despliega desde `miguelcabezas03/Planingtools`; el
mismo código se mantiene también en `3xotiC777/planningtools`.

La interfaz web está en `web_app.py`. Reutiliza los motores Python para
depuración, selección, rutas, cruces geográficos, mallas y distancias.
Cada usuario carga sus Excel y capas desde el navegador y descarga los
resultados. Las bases operativas, los resultados y los logs siguen fuera de
GitHub. Los archivos temporales de cada ejecución se eliminan al terminar.

Para ejecutarla en un servidor o localmente:

```bash
pip install -r requirements-web.txt
streamlit run web_app.py
```

El repositorio incluye `Dockerfile` y `render.yaml` para crear un servicio web
Python desde Render. El plan gratuito sirve para validar con archivos pequeños;
los universos grandes necesitan más memoria y CPU. El servicio existente toma
su código de `main` en `miguelcabezas03/Planingtools`, mediante la URL
pública del repositorio: después de cada cambio se debe elegir **Manual
Deploy → Deploy latest commit** en Render. `/_stcore/health` permite comprobar
su estado.
La URL es pública y todavía no exige inicio de sesión: antes de cargar datos
confidenciales, se deben habilitar controles de acceso apropiados.

La versión de escritorio continúa disponible mediante
`Ejecutar Planning Tools.bat`. Su instalación usa `requirements.txt`.

Aplicación de escritorio para Windows con módulos de configuración,
depuración de universo, selección de muestra, optimización de rutas y manejo
de polígonos.

## Repositorio y parámetros

- El código fuente y el historial de cambios se mantienen en GitHub.
- Los parámetros de la aplicación se encuentran en `Config/config.json`.
- Los cambios hechos desde la interfaz quedan guardados en ese archivo. Para
  conservarlos también en GitHub se debe realizar un `commit` y `push`.
- Los entornos de Python, logs, archivos de entrada, resultados y bases
  operativas están excluidos del repositorio mediante `.gitignore`.
- Cada usuario instala sus dependencias una sola vez con
  `Instalar Planning Tools.bat` y abre la aplicación con
  `Ejecutar Planning Tools.bat`.

Todos los mapas utilizan una base cartográfica de OpenStreetMap con apariencia
de mapa de calles: ciudades, vías, agua, terreno y fronteras. Las imágenes se
guardan en caché local para reutilizarlas; si no hay internet, se conservan los
puntos, polígonos y contornos disponibles. Se muestra siempre la atribución
`© OpenStreetMap contributors`. La proyección conserva la relación de aspecto
del mapa en cualquier tamaño de ventana para evitar países estirados o
comprimidos.

## Optimización de rutas

El módulo incluye dos opciones independientes con el formato visual nativo de
Planning Tools:

- **Nueva planificación:** carga una base de puntos y un forecast mensual,
  forma jornadas geográficas compactas por MT FINAL, respeta los cupos por día,
  asigna suplentes con prioridad y cercanía, permite mover puntos manualmente y
  exporta el resultado conservando el libro base.
- **Puntos en Excel:** abre cualquier Excel con LATITUD/LONGITUD, permite
  agrupar, filtrar, seleccionar por área, editar campos, crear columnas y
  guardar una copia.

## Cruce y generador de polígonos

Contiene dos subpestañas:

- **Cruce y dibujo:** centraliza la carga de GeoPackage, Shapefile, GeoJSON o
  Excel WKT; el dibujo de rectángulos y polígonos libres; el cruce
  punto-en-polígono y la exportación. El mapa permanece visible aun antes de
  cargar datos y permite crear áreas por vértices con **Terminar polígono**, o
  mediante **Mano alzada** manteniendo presionado el botón principal. Después
  de dibujarlas, **Editar figura** permite moverlas, redimensionarlas desde las
  esquinas o achicarlas y agrandarlas en pasos del 10%.
- **Generador de mallas:** recibe un Excel de PDV y un GeoPackage nacional,
  permite seleccionar hoja, coordenadas, capa y campo de clasificación. Se
  puede generar con **cuadrados** o **hexágonos regulares** indicando la
  longitud del lado, o con **círculos** indicando el radio; la medida inicial
  es 300 m y puede cambiarse a 200 m o a cualquier valor positivo. Filtra `Zona Urbana`, conserva las
  zonas a máximo 15 km de los PDV y exporta en EPSG:4326 únicamente los
  elementos que contienen un PDV o intersectan una de esas zonas. El cálculo
  selecciona automáticamente la zona UTM para respetar las medidas métricas.

Al pasar el cursor sobre una herramienta se muestra una explicación breve de
su función.

## Matriz de distancias

Reemplaza el módulo anterior de asignación de rutas y contiene tres
subpestañas, con la misma navegación de Optimización de rutas:

- **Por columna puente:** selecciona dos archivos y hojas, permite elegir en
  cada uno el código/ruta/ID de cruce, el GPS y las columnas finales. Exporta
  **RESUMEN** y **BD** como `Comparativa_Distancias_GPS.xlsx`.
- **Por cercanía:** no necesita llave común. Para cada punto del primer
  archivo encuentra el punto geográfico más cercano del segundo mediante un
  índice espacial y entrega la distancia en metros.
- **Ruta ordenada:** selecciona una base, su GPS y la columna que reinicia cada
  recorrido o clúster (por ejemplo COMUNA, RUTA o AGENCIA). Genera
  `Orden_Ruta`, `Distancia_Punto_Anterior_Metros`, la hoja
  **Datos_Ordenados_Ruta** y un resumen de distancia total por grupo.

Las tres opciones calculan con Haversine y expresan las distancias siempre en
metros. La lectura se limita a las columnas necesarias y la exportación usa
escritura continua para controlar el consumo de memoria.

## Estructura
```
Planning Tools/
├── Planning Tools.exe      <- (generado con PyInstaller)
├── Instalar Planning Tools.bat
├── Ejecutar Planning Tools.bat
├── requirements.txt
├── Entrada Depuracion/     <- universos e incidencias por país
├── Entrada Seleccion/      <- insumos opcionales para selección
├── Salida Depuracion/      <- universos elegibles y exclusiones
├── Salida Muestras/        <- titulares, suplentes y auditoría
├── Logs/                   <- bitácora de cada ejecución (usuario, tiempos, errores)
└── Config/                 <- configuración por país y logo corporativo
```

## Uso (usuario final)
1. Ejecutar una sola vez `Instalar Planning Tools.bat`. Si Python no existe,
   el instalador intenta agregar Python 3.12 mediante `winget`; después crea
   un entorno privado `.venv` e instala todas las dependencias.
2. Abrir la aplicación con `Ejecutar Planning Tools.bat`.
3. Elegir el país y revisar archivos, hojas y mapeo en **Configuración**.
4. Ejecutar **Depuración de Universo**. El mapa muestra puntos elegibles y
   exclusiones REP, de incidencias y geográficas. En la revisión se dibuja el
   límite político del país; use **✋ Mover mapa** para navegar sin seleccionar,
   **Seleccionar área** para marcar puntos y el control **Tamaño de puntos**
   para ajustar su visibilidad.
5. Ejecutar **Selección de muestra** y abrir las salidas desde la aplicación.
6. En **Optimización de rutas**, use **Nueva planificación** para combinar base
   y forecast, o **Puntos en Excel** para inspeccionar y editar una base libre.
7. En **Cruce y generador de polígonos**, use **Cruce y dibujo** para editar
   áreas, o **Generador de mallas** para crear cuadrados, hexágonos o radios de cobertura.
8. En **Matriz de distancias**, seleccione los dos Excel, calcule y exporte la
   matriz origen-destino.

Si la hoja del universo cambia de nombre en el futuro (p. ej. `BASE ICE SEP`),
editar `Config/config.json` -> `"hoja_universo"` con el Bloc de notas. Lo mismo
aplica a las reglas (`ufa_anio`, `estudio_pais`, etc.). No se recompila nada.

## Lógica de Código Puente (importante)
El `Id_PDV` de incidencias (prefijos 77/78) NO coincide literalmente con el
`CÓDIGO` del universo (prefijos 27/28); coincide dígito a dígito con la columna
`Codigo DN`. Por eso el cruce es **exacto `Id_PDV <-> Codigo DN`** (configurable
en `columna_puente`). Si esa columna no existiera, la app usa como respaldo los
últimos `n_digitos_cruce` dígitos. La deduplicación de incidencias se hace por
`Id_PDV` completo ANTES del cruce para no perder registros de Guatemala frente
a otros países con sufijos iguales.

Casos generados automáticamente sin borrar el código original:

- Costa Rica: `Codigo D&N = 150 + últimos 7 dígitos` cuando el código base
  tiene 9 dígitos; para las demás longitudes usa `154 + últimos 7 dígitos`.
- Nicaragua: `Codigo D&N = 160 + últimos 7 dígitos`.

## Arquitectura (SOLID, un módulo = una responsabilidad)
| Módulo | Responsabilidad |
|---|---|
| `main.py` | Arranque, inicialización, manejo de errores fatales |
| `interfaz.py` | GUI CustomTkinter, hilos + cola (UI nunca se congela) |
| `lector_excel.py` | Lectura robusta de Excel con errores claros |
| `validaciones.py` | Archivos, hojas, columnas, vacíos, duplicados |
| `normalizacion.py` | Limpieza vectorizada de llaves (500k+ filas) |
| `depuracion.py` | Pipeline de negocio (clase `DepuradorUniverso`) |
| `optimizacion_rutas.py` | Motor geográfico, forecast, suplentes, QA vial y exportación |
| `herramientas_geograficas.py` | Carga, cruce y exportación de puntos/polígonos |
| `generador_mallas.py` | Filtro urbano, buffer operativo, malla métrica e interfaz de exportación |
| `modulos_geograficos.py` | Interfaz de optimización, puntos Excel y polígonos |
| `mapa_base.py` | Fondo cartográfico común, proyección Web Mercator y caché |
| `matriz_distancias.py` | Carga de dos bases, Haversine y exportación de la matriz |
| `reportes.py` | Excel de salida + `Resumen.pdf` (Pillow) |
| `logs.py` | Bitácora con usuario, equipo y tiempos |
| `utilidades.py` | Rutas (script/exe), configuración, helpers |

Los módulos futuros se agregan registrándolos en `REGISTRO_MODULOS`
(`interfaz.py`) con su propio frame, sin tocar la arquitectura.

## Compilar el ejecutable (una sola vez, en Windows)
```
:: Después de ejecutar el instalador, en CMD dentro de la carpeta:
.venv\Scripts\python.exe -m PyInstaller "Planning Tools.spec"
```
El ejecutable queda en `dist\Planning Tools.exe`. Copiarlo a la carpeta
`Planning Tools` junto a las carpetas `Entrada Depuracion/`, `Entrada Seleccion/`,
`Poligonos Muestras/`, `Logs/` y `Config/` (la app crea las carpetas que falten
en su primer arranque). Windows Defender puede marcar
un falso positivo típico de PyInstaller: agregar excepción si ocurre.

## Etapa de rotación (nueva)
Tras el cruce de incidencias, el pipeline aplica la elegibilidad por rotación
de GEC: `Cupo_Restante = cupo_anual(GEC) − A_Actual`. Tiendas con cupo agotado
salen del elegible (archivo `Tiendas_Sin_Cupo.xlsx`) salvo que sean fijas.
- Los límites ORO/PLATA/BRONCE se editan en **Configuración → Muestra
  Selección → 4. REP**. Esos mismos valores alimentan directamente
  `rotacion.cupos_por_gec` y la condición `NO ELEGIBLE REP`. La comparación
  usa `A_Actual >= límite` y reconoce las categorías sin distinguir
  mayúsculas, minúsculas, espacios ni el sufijo `REP`.
- Fijos: listado opcional `Entrada/Fijos.xlsx`; se detecta cualquier columna
  cuyo nombre contenga 'COD' (CÓDIGO, Codigo DN, Cod Cliente).
- El `Universo_Elegible.xlsx` ahora incluye `A_Actual`, `A_Total`,
  `Cupo_Anual`, `Cupo_Restante` y `Es_Fijo` — insumos directos para la
  priorización en el módulo de Selección de muestra.
- Si la base de incidencias no trae `A_Actual`/`A_Total`, la etapa se omite
  con aviso en el log (no bloquea la depuración).

## Módulo Selección de Muestra (nuevo)
Consume `Salida/Universo_Elegible.xlsx` y genera `Muestra_Titulares.xlsx`,
`Muestra_Suplentes.xlsx` y `Auditoria_Muestra.xlsx`. Diseño:
- Clusterización DBSCAN sobre latitud/longitud antes de seleccionar. El motor
  calcula `CLUSTER`, `DENSIDAD` y prioriza los núcleos con más PDV sin cambiar
  el tamaño, las cuotas ni los límites por ruta configurados. `EPS` y
  `Min Samples` se editan desde Configuración.
- OR-Tools conserva como restricciones duras las cuotas configuradas de GEC,
  canal, tipo/fijo y, cuando aplica, región, subcanal o agencia. Si debe usar
  el motor de respaldo, las rutas y los puntos con mayor densidad tienen
  prioridad.
- Fijos ('Cliente fijo'=SI) entran obligatorios y fuerzan sus rutas (pueden
  exceder el máximo de ruta; queda registrado).
- Coordenadas: se normalizan automáticamente (columnas LATITUD/LONGITUD
  intercambiadas y escalas 10^k mezcladas) contra `limites_pais`.
- Dispersión: columna `Dist_T_Cercano_km` por titular (haversine) e índice
  promedio en la auditoría.
- Suplentes: relación configurable 1:4 o 1:5 (`ratio_suplentes`); mismo GEC,
  prioridad misma ruta y cercanía; etiquetas S1..Sn por distancia, columnas
  `Titular_CODIGO` y `Distancia_al_Titular_km`; cada suplente se usa una vez.
- `semilla` fija la reproducibilidad (null = aleatorio por corrida).
- Todos los parámetros por país en `Config/config.json` -> `modulo_seleccion`
  (tamaño, cuotas, mín/máx/promedio por ruta, límites geográficos).

## Rendimiento
Operaciones 100% vectorizadas (pandas): probado con 40,745 tiendas + 150,000
incidencias en ~45 s (la mayor parte es lectura/escritura de Excel con
openpyxl). Diseñado para 500,000 tiendas / 300,000 incidencias.
