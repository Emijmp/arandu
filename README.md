# Arandu

Aplicación **nativa de escritorio** para organizar y leer tus libros PDF y EPUB. Está hecha con Python y Qt Widgets: abre su propia ventana, usa selectores de archivos del sistema y guarda el catálogo en SQLite.

## Aplicación instalada

La compilación **Linux x86_64** está instalada para este usuario. Buscá **Arandu** en el menú del sistema o en tu launcher de aplicaciones. En el gestor de archivos, elegí **Abrir con → Arandu** para PDF o EPUB; también se pueden abrir varios archivos juntos. Si Arandu ya está abierto, los libros llegan a esa misma instancia. Las aplicaciones predeterminadas anteriores se conservan.

También podés iniciarla directamente:

```sh
~/.local/bin/arandu
~/.local/bin/arandu "/ruta/Libro.pdf" "/ruta/Otro libro.epub"
```

El ejecutable está en `~/.local/opt/arandu/arandu` y el registro del menú en `~/.local/share/applications/arandu.desktop`. Incluye Python, Qt, tipografías, logo, modelos OCR, Poppler y Tesseract. No requiere instalar paquetes Python para usar esta compilación en este equipo. El paquete completo está en `dist/arandu/`; su ejecutable necesita conservar al lado la carpeta `_internal`.

La aplicación instalada guarda sus datos en `~/.local/share/arandu/`. Durante la instalación se copió el catálogo anterior de `.arandu/`, conservando el original. Las actualizaciones del ejecutable no reemplazan el catálogo del usuario.

## Iniciar desde el código

```sh
python3 arandu.py
```

Se abre la ventana de Arandu. En este equipo ya están disponibles las dependencias.

Este modo usa `.arandu/` dentro del proyecto. Para trabajar con el catálogo de la aplicación instalada, usá `python3 arandu.py --data-dir ~/.local/share/arandu`.

En otra computadora necesitás **Python 3.11 o posterior** con SQLite FTS5, **PyQt5** y **Poppler** (`pdfinfo`, `pdftoppm` y `pdftotext`) para procesar PDF:

```sh
python3 -m pip install -r requirements.txt
```

Poppler se instala con el gestor de paquetes del sistema: `poppler` en Arch Linux, `poppler-utils` en Debian/Ubuntu o `poppler` con Homebrew en macOS. En Windows sus ejecutables deben estar en `PATH`. La interfaz y las pruebas se verificaron en Linux.

Para guardar el catálogo en otra ubicación:

```sh
python3 arandu.py --data-dir /ruta/a/mi-biblioteca
```

## Volver a compilar

Con PyQt5, Poppler y Tesseract disponibles, instalá PyInstaller en un entorno de compilación y ejecutá:

```sh
python3 tools/build_desktop.py --install
```

El script construye el paquete, verifica apertura PDF/EPUB e instancia única, comprueba las herramientas nativas incluidas y recién después instala el ejecutable y actualiza menú, icono y asociaciones «Abrir con». `--skip-build --install` verifica e instala un paquete ya generado. La instalación es por usuario y conserva el catálogo existente.

## Organizar tu biblioteca

- **Añadir libros:** seleccioná PDF y EPUB con el selector del sistema o arrastralos a la ventana. Se registran sus rutas originales; los archivos permanecen en su ubicación.
- **Conectar carpeta:** elegí una carpeta y revisá sus subcarpetas y PDF/EPUB antes de importar. Podés ignorar las rutas que no te interesan y pulsar **Guardar y actualizar**. La vigilancia de carpetas y archivos detecta incorporaciones y cambios en las fuentes incluidas, con una revisión adicional cada minuto. Podés desactivarla en **Biblioteca → Mantenimiento → Sincronizar carpetas automáticamente**. `F5` mantiene disponible el escaneo manual, siempre en segundo plano.
- **Buscar y filtrar:** buscá por título, autor, etiqueta, categoría, colección o serie. **Filtros…** reúne formato, categorías, etiquetas y orden. El selector **Estantes** permite cambiar de colección o estante inteligente; el control contiguo **Estante / Lista** alterna la presentación. **En el texto** busca dentro de los libros.
- **Favoritos y lecturas:** seleccioná un libro para marcarlo como favorito. **Ficha y etiquetas** permite editar título, autor, categorías, etiquetas, notas generales, porcentaje, estado, colecciones y serie con su número de orden.
- **Etiquetas y categorías:** en **Ficha y etiquetas**, escribí varios nombres separados por comas. Las categorías pueden ser «Novela» o «Historia»; las etiquetas, «Paraguay» o «Investigar». Al escribir aparecen sugerencias de nombres existentes. También se accede con clic derecho → **Etiquetas y categorías…**. Se admiten hasta 30 etiquetas y 30 categorías por libro, con nombres de hasta 60 caracteres; las mayúsculas no crean duplicados.
- **Filtrar por clasificación:** **Filtros…** combina una categoría y una etiqueta con la búsqueda, formato, colección y estado actuales. El botón indica cuántos filtros de clasificación están activos. **Limpiar filtros** dentro del diálogo los restablece al pulsar **Aplicar**; **Ver todos los libros** en un resultado vacío quita todos los filtros. El estante y la lista muestran las categorías y etiquetas de cada libro, con el detalle completo al pasar el mouse.
- **Gestionar nombres:** **Biblioteca → Organizar → Etiquetas y categorías…** permite crear, renombrar y eliminar nombres. Un cambio de nombre se aplica a todos sus libros. Eliminarlo solo quita esa clasificación: conserva libros, notas y archivos. Las etiquetas anteriores se conservan y las categorías se incorporan al catálogo automáticamente.
- **Colecciones:** creá, renombrá o quitá estantes en **Biblioteca → Organizar → Colecciones…**. Un libro puede pertenecer a varios; quitar una colección conserva sus libros y sus notas.
- **Leer:** hacé doble clic en un libro o usá **Leer libro**. PDF y EPUB recuerdan la última página o sección abierta. **Marcar leído** completa el progreso al 100 %.
- **Quitar del catálogo:** mueve la ficha a la papelera y conserva el archivo, sus notas y su progreso. El escaneo no vuelve a incorporarla mientras esté en la papelera.

Se extraen título y autor de los metadatos de PDF y EPUB cuando existen. Los EPUB usan su portada incluida y los PDF generan una miniatura de la primera página. Si falta el título, se usa el nombre del archivo. Las correcciones manuales se conservan al actualizar. **Biblioteca → Mantenimiento → Actualizar portadas e índice** reprocesa la biblioteca existente.

**Biblioteca → Mantenimiento → Revisar duplicados…** agrupa los archivos con contenido idéntico mediante SHA-256, aunque tengan distinto nombre. La app conserva las fichas y los archivos para que puedas revisarlos. Si un libro se mueve dentro de las carpetas conectadas, se recupera su nueva ruta conservando su ficha, notas, progreso y anotaciones. Para otras ubicaciones, usá **Localizar archivo…** en su ficha y guardá los cambios.

## Ocultar libros e ignorar archivos o subcarpetas

**Ocultar** aparta una ficha del estante habitual y de las búsquedas generales, conservando sus etiquetas, notas, progreso e historial de lectura. Seleccioná uno o varios libros y elegí clic derecho → **Ocultar del estante**; también está la casilla **Ocultar del estante habitual** dentro de la ficha. **Biblioteca → Organizar → Libros ocultos… → Mostrar selección** los recupera. Ocultar no excluye el archivo de las fuentes: al actualizar su carpeta, sigue al día y permanece oculto.

**Ignorar** excluye una ruta de las importaciones, los escaneos manuales, la sincronización y la vigilancia. Las subcarpetas ignoradas no se recorren y la exclusión incluye todos sus niveles. Un archivo ignorado se salta incluso al añadirlo directamente. Si ya estaba catalogado, su ficha queda guardada y apartada del estante; vuelve con sus notas y progreso al incluirlo otra vez. Las exclusiones corresponden a rutas, mientras que ocultar es una propiedad de la ficha.

Abrí **Archivo → Carpetas e ignorados…** (`Ctrl+Shift+E`) para revisar las fuentes y sus archivos. Expandí las carpetas, seleccioná archivos o subcarpetas con `Ctrl`/`Shift` y pulsá **Ignorar selección**. La pestaña **Ignorados** reúne todas las exclusiones, incluidas rutas no disponibles. **Volver a incluir** quita una exclusión; si se hereda de una carpeta superior, se indica qué carpeta debés incluir primero. **Guardar y actualizar** aplica todos los cambios y escanea las fuentes; **Cancelar** los descarta.

Por ejemplo, conectá `Libros`, ignorá dos de sus cinco subcarpetas y uno de sus dos PDF sueltos. Quedarán en uso las otras tres subcarpetas y el PDF elegido. Los archivos que aparezcan después dentro de una carpeta ignorada también se omiten. **F5** y la sincronización conservan esta selección.

Desde el estante también podés seleccionar libros y usar clic derecho → **Ignorar estos archivos**. Se recuperan desde la pestaña **Ignorados**. Ignorar u ocultar no borra archivos ni manda fichas a la papelera. **Desconectar** una carpeta detiene su escaneo, conservando sus fichas visibles; si también querés apartarlas, ignorá la carpeta. Los estados ocultos y las exclusiones se guardan en el catálogo y se incluyen en las copias de seguridad. Al recuperar un libro ignorado en una ubicación nueva desde una copia portable, se añade una exclusión para su ruta recuperada para que no reaparezca por accidente.

## Organizar varios libros

Seleccioná varios libros con `Ctrl` o un rango con `Shift`. **Editar N libros**, **Biblioteca → Organizar → Editar selección…** y `Ctrl+Shift+I` abren la edición por lotes. Solo se modifican los campos marcados: autor, estado, favoritos, etiquetas, categorías y colecciones. Los nombres se pueden añadir, reemplazar o quitar. La operación se guarda completa o se revierte completa si hay un error. El botón de favoritos también actúa sobre toda la selección.

**Biblioteca → Organizar → Estantes inteligentes…** permite guardar búsquedas y filtros por estado, formato, categoría, etiqueta y colección. El editor muestra cuántos libros coinciden y parte de los filtros actuales. Los estantes aparecen junto con las colecciones en el selector **Estantes** y se actualizan al cambiar los libros; no crean copias. Al elegirlos, la búsqueda y los filtros visibles pueden limitar todavía más sus resultados. Renombrar una etiqueta, categoría o colección actualiza las referencias de los estantes y las reglas.

**Biblioteca → Organizar → Reglas de organización…** añade etiquetas, categorías o colecciones al incorporar o actualizar archivos. Podés combinar carpeta y subcarpetas, texto en el nombre del archivo, texto en el autor y formato; se deben cumplir todas las condiciones indicadas. Las reglas se pueden pausar y no reemplazan nombres existentes. **Aplicar a la biblioteca** también procesa los libros ya incorporados. Eliminar una regla conserva las clasificaciones que había asignado.

## Historial y deshacer

**Edición → Historial de cambios…** (`Ctrl+Alt+H`) muestra las acciones recientes con sus valores anteriores y nuevos. **Deshacer último** y **Rehacer** también están disponibles desde el menú Edición con `Ctrl+Z` y `Ctrl+Shift+Z`.

Se registran correcciones de título, autor, estado de lectura, progreso editado en la ficha, etiquetas, categorías, colecciones, serie, visibilidad, notas y anotaciones. Cada edición por lotes y cada **Aplicar reglas a la biblioteca** se guarda como una sola acción, incluso cuando afecta a varios libros. Deshacer una aplicación recupera las clasificaciones anteriores y mantiene la regla disponible; podés pausarla desde Reglas de organización para evitar futuras aplicaciones. Renombrar o eliminar clasificaciones también permite recuperar sus referencias en estantes y reglas.

El historial se guarda en SQLite y viaja en las copias de seguridad. Los cambios se deshacen en orden inverso; una edición nueva después de deshacer abre una continuación nueva y los cambios pendientes de rehacer quedan indicados como reemplazados. Si una ficha fue modificada por otra vía y sus valores ya no coinciden, la acción se detiene completa para conservar los datos actuales. Moverse por páginas y capítulos no añade entradas al historial ni deshacer una clasificación cambia el punto exacto de lectura.

## Portadas y ediciones

En **Ficha y etiquetas → Portada → Cambiar…**, elegí una imagen PNG, JPEG, WebP o BMP. Se muestra una miniatura antes de guardar; cancelar conserva la portada anterior. Se admiten imágenes de hasta 25 MB y 40 megapíxeles, normalizadas a un PNG de hasta 1000 × 1400. **Del archivo** recupera la portada del EPUB, la primera página del PDF o la portada tipográfica de respaldo. Actualizar el índice no reemplaza una portada personalizada.

Para reunir distintos formatos o ediciones de una obra, seleccioná sus fichas y abrí **Biblioteca → Organizar → Obras y ediciones… → Agrupar selección como obra nueva**. La obra aparece como una sola ficha con sus formatos. Al abrirla, elegí qué edición leer; **Preferir** determina qué edición representa la obra en el estante. Se pueden añadir archivos a una obra, consultar cada ficha o separar una edición. Cada archivo conserva su progreso y sus anotaciones. Las búsquedas y los filtros se aplican a las ediciones antes de agruparlas. Las estadísticas cuentan una obra agrupada una sola vez al año aunque termines más de una edición.

## Tiempo de lectura y objetivos

**Biblioteca → Lectura y notas → Lecturas y objetivos…** muestra libros terminados, tiempo y sesiones por mes, además de las sesiones recientes. El lector registra el tiempo localmente, guarda avances cada 15 segundos y al cerrar, y pausa el registro cuando cambiás de ventana, minimizás o pasan cinco minutos sin interacción. Las estadísticas empiezan a registrar tiempo desde esta versión; no inventan sesiones anteriores. Para libros terminados antes de disponer de esta función se usa, con una indicación visible, la última modificación como fecha estimada.

Podés guardar una meta anual de libros y una meta diaria de minutos para cada año. Cero desactiva una meta. El seguimiento es discreto: no genera notificaciones ni envía datos a servicios externos.

## Papelera y exportación de notas

**Biblioteca → Organizar → Papelera del catálogo…** permite restaurar varias fichas con sus etiquetas, notas, progreso, ediciones y anotaciones. **Eliminar definitivamente** requiere confirmación y elimina esos datos de Arandu; el archivo original sigue en su carpeta. La papelera también está incluida en las copias de seguridad.

**Biblioteca → Lectura y notas → Exportar anotaciones…** guarda notas generales, citas completas, resaltados y marcadores como Markdown o TXT, con título, autor, formato y página o sección. Si hay una selección, se exportan esos libros; si no, toda la biblioteca. Desde el lector, **Lectura → Exportar anotaciones…** exporta solo el libro abierto.

## Buscar dentro de los libros

El botón **En libros…** o **Biblioteca → Lectura y notas → Buscar en el contenido…** abre la búsqueda en todo el texto indexado. Podés buscar palabras sin distinguir acentos o escribir una `"frase exacta"`. Cada resultado incluye el libro, la página o sección y un fragmento; al abrirlo, el lector salta a esa ubicación. Desde un libro abierto, **Buscar** limita los resultados a ese libro.

La extracción y el índice funcionan localmente. Para PDF escaneados está disponible el reconocimiento integrado que se describe a continuación. No se admiten libros con DRM ni PDF que requieran contraseña.

## OCR local para PDF escaneados

Seleccioná un PDF y abrí **Biblioteca → Mantenimiento → Reconocer texto (OCR)…**, o **Lectura → Reconocer texto (OCR)…** dentro del lector PDF. Elegí español o inglés y pulsá **Reconocer texto**. El proceso corre en segundo plano, indica su progreso y se puede detener al terminar la página actual, conservando las páginas procesadas. Se omiten páginas con texto extraíble o con OCR ya guardado.

El texto y las coordenadas de las palabras se guardan en el catálogo: permiten buscar, copiar, resaltar y anotar el escaneo desde Arandu. **El PDF original no recibe una capa de texto ni se modifica.** Actualizar el catálogo y restaurar una copia conserva el reconocimiento cuando el contenido del PDF sigue siendo el mismo. La precisión depende de la calidad del escaneo.

El motor es [Tesseract](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html), junto con Poppler para renderizar las páginas. En este equipo está disponible. En otra computadora necesitás el paquete `tesseract` (Arch o Homebrew) o `tesseract-ocr` (Debian/Ubuntu), con su ejecutable en `PATH`. Los modelos oficiales `tessdata_fast` de español e inglés se incluyen en `assets/ocr/tessdata/`, con su licencia Apache 2.0 y referencias en `assets/ocr/README.md`. El reconocimiento no descarga modelos ni consulta servidores.

## Lectura y anotaciones

- **PDF:** seleccioná texto con el mouse o `Ctrl+A` para toda la página y copialo con `Ctrl+C`. La selección usa las coordenadas de las palabras del documento, incluso al cambiar el zoom.
- **Marcador:** guardá o quitá la página o sección actual. Los marcadores persisten al cerrar el libro.
- **Resaltar:** seleccioná un fragmento y guardalo como resaltado, tanto en PDF como en EPUB.
- **Nota:** añadí una nota vinculada a la ubicación actual y, si hay una selección, a ese fragmento.
- **Anotaciones:** recorré los marcadores, resaltados y notas; saltá a sus ubicaciones, editá sus notas o quitá una anotación.
- **Lectura → Pantalla completa:** oculta las herramientas para leer. `F11` activa o desactiva este modo; `Esc` vuelve a la ventana normal.
- **EPUB → Lectura → Letra, márgenes e interlineado…:** ajustá esos tres valores con vista previa. Guardalos para próximas lecturas o cancelá para recuperar los anteriores.

### Reanudar en el punto exacto

Cada EPUB recuerda el capítulo y el punto visible dentro del texto. Al cambiar el ancho de la ventana, se usa una posición del documento para conservar la referencia de lectura. Cada PDF recuerda la página, el zoom y el desplazamiento horizontal y vertical dentro de ella. La posición se guarda durante la lectura y al cerrar, también al salir con `Esc`.

Las búsquedas, los enlaces del índice y las anotaciones abren su ubicación solicitada; al volver a abrir normalmente el libro, se recupera el último punto guardado. Los datos están en el catálogo y se conservan al restaurar una copia o reparar la ruta del mismo archivo.

### Navegación lateral y EPUB paginado

El lector muestra un panel lateral en ventanas amplias. Para EPUB utiliza el índice jerárquico del documento, incluidos apartados y enlaces a puntos dentro de un capítulo; también admite índices NCX. Si el libro no incluye un índice, usa su lista de secciones. Para PDF muestra miniaturas, que se generan solo para la zona visible y usan una caché acotada. Elegir una miniatura abre su página.

**Lectura → Mostrar navegación lateral** (`Ctrl+T`) permite ocultar o recuperar el panel. En ventanas pequeñas y durante la pantalla completa se aparta para dar espacio al libro, respetando la preferencia al ampliar la ventana.

**Lectura → Presentación EPUB** ofrece tres modos por libro: **Desplazamiento continuo**, **Página individual** y **Doble página**. Los modos paginados presentan páginas reales del documento con selección de texto, copia, enlaces, búsqueda, notas y resaltados. **Anterior/Siguiente**, `Page Up`/`Page Down` y la rueda cambian de página; al llegar al límite se pasa al capítulo contiguo. El contador corresponde a las páginas de la sección actual y se recalcula al cambiar letra, márgenes o tamaño de ventana. La presentación elegida también se recuerda al reabrir el libro.

### Todas las anotaciones

**Biblioteca → Lectura y notas → Todas las anotaciones…** (`Ctrl+Alt+N`) reúne las notas generales de las fichas, notas de lectura, resaltados y marcadores. Buscá por título, autor, cita o nota sin distinguir acentos, y filtrá por libro o tipo. La selección muestra la cita y nota completas. **Abrir en el lector** lleva a la página, capítulo o fragmento correspondiente; también podés editar o eliminar una nota, con registro en el historial.

Los libros ocultos e ignorados se incluyen solo cuando marcás **Incluir ocultos e ignorados**. Si falta el archivo, sus notas siguen disponibles y el botón de apertura indica que no puede leerse todavía.

## Diagnóstico y reparación por lotes

**Biblioteca → Mantenimiento → Diagnóstico y reparación… → Revisar** comprueba archivos faltantes, errores al abrir PDF o EPUB, cambios sin indexar, errores de extracción y documentos sin texto para buscar. Incluye fichas ocultas y respeta las exclusiones de fuentes. El trabajo se hace en segundo plano y se puede detener al terminar el archivo actual.

Seleccioná varios problemas con `Ctrl`/`Shift` y usá **Buscar archivos movidos…** para revisar una carpeta y sus subcarpetas. Solo se enlazan archivos del mismo formato con el mismo hash de contenido; se conservan las fichas, notas, anotaciones, historial y posición exacta. Los archivos distintos o ya registrados como otra ficha quedan sin enlazar, con un informe de lo que no se pudo resolver. Las fichas antiguas sin hash verificable pueden localizarse individualmente desde **Abrir ficha → Localizar archivo…**.

**Reindexar selección** vuelve a extraer metadatos y texto de los archivos disponibles. **Reconocer texto (OCR)…** abre el reconocimiento para el PDF seleccionado cuando no tiene texto. La revisión posterior muestra lo que continúa pendiente; cancelar conserva los resultados ya aplicados y permite volver a revisar. La sincronización automática espera mientras estás reparando el catálogo.

Las anotaciones se guardan en el catálogo y se muestran sobre el libro; los archivos PDF y EPUB originales permanecen intactos.

## Apariencia

La interfaz adopta un **catálogo editorial sobrio**: búsqueda y estantes en una barra, controles secundarios agrupados y una marca de color junto al lomo para seleccionar libros. Usa Montserrat en encabezados, Karla en texto, navegación lateral discreta y un catálogo centrado en las portadas. Los controles de lectura y edición aparecen al seleccionar un libro. Las fuentes se incluyen con sus licencias y funcionan sin conexión.

El nombre es **Arandu**, sin acento. El logo suministrado se integra como SVG: versión horizontal en la barra lateral y símbolo de la A con libro en la vista compacta y los iconos de ventana. Sus colores cambian con cada una de las seis paletas. Las variantes exportadas están en `assets/branding/themes/`.

Elegí un tema en el selector de la esquina superior derecha o en **Apariencia**. La elección se guarda en cuanto la cambiás y se recupera al abrir la aplicación.

| Claros | Paleta | Oscuros | Paleta |
| --- | --- | --- | --- |
| Papel | Marfil y granate | Nogal (paleta 1) | Negro cálido, arena y marfil |
| Porcelana | Blanco y azul de tinta | Grafito (paleta 2) | Carbón frío y gris plata |
| Lino | Papel natural y verde | Tinta (paleta 3) | Azul noche y azul bruma |

Los temas oscuros siguen las tres referencias proporcionadas: Nogal usa `#0A0A0C`, `#1F1C1A`, `#5C5048`, `#C8B29A` y `#F3EFE9`; Grafito usa `#101216`, `#2A2F37`, `#474F5B`, `#B7BEC8` y `#F3F5F8`; Tinta usa `#0E1A2B`, `#1B3454`, `#274472`, `#5C7AA3` y `#A4B7D7`. Los colores de la tercera paleta se tomaron directamente de la imagen.

El cambio se aplica al catálogo, los diálogos y los lectores abiertos sin perder el libro ni su posición. El texto EPUB adopta la paleta elegida. Las páginas PDF conservan los colores del documento original; el tema cambia los controles y el fondo del lector.

Para crear una paleta propia, abrí **Apariencia → Personalizar tema…** o elegí **Personalizar…** en el selector. Podés partir de un tema existente, darle un nombre y ajustar fondo, superficies, texto, texto secundario, acento, selección y bordes con un selector de color o códigos hexadecimales.

La vista previa es inmediata y también actualiza el logo, los iconos y los lectores. **Cancelar** recupera el tema anterior; **Restablecer** recupera los colores de la paleta de partida. El editor comprueba el contraste y señala qué combinación necesita ajustarse antes de guardar.

**Guardar tema** incorpora la paleta al selector y a **Apariencia → Mis temas**. Para editarla, seleccionala y volvé a abrir el editor. Para crear otra, partí de una de las seis paletas originales; estas se conservan intactas. Los temas propios se guardan en `.arandu/themes.json`, y la elección activa se recuerda al reiniciar.

**Apariencia → Gestionar temas y tamaño de texto…** permite duplicar paletas, importar y exportar archivos JSON, eliminar temas personales y ajustar el texto de la aplicación al 90 %, 100 %, 110 % o 125 %. Las seis paletas originales se conservan. Los temas importados reciben nombres e identificadores propios, sin reemplazar los existentes.

La ventana se adapta desde **680 × 560**. En ventanas pequeñas, el estante se presenta como un índice compacto para mantener legibles las fichas; al ampliarla recupera la vista elegida. Hay foco visible, navegación por teclado y contraste de texto de al menos 4,5:1 en las combinaciones principales de las seis paletas. Los estados de importación, error y éxito aparecen junto al catálogo.

## Atajos

| Acción | Atajo |
| --- | --- |
| Añadir libros | `Ctrl+O` |
| Conectar carpeta | `Ctrl+Shift+O` |
| Gestionar fuentes e ignorados | `Ctrl+Shift+E` |
| Actualizar carpetas | `F5` |
| Buscar | `Ctrl+F` |
| Buscar en el contenido de todos los libros | `Ctrl+Shift+F` |
| Leer el libro seleccionado | `Enter` |
| Detalles | `Ctrl+I` |
| Editar selección de libros | `Ctrl+Shift+I` |
| Deshacer / rehacer un cambio del catálogo | `Ctrl+Z` / `Ctrl+Shift+Z` |
| Historial de cambios | `Ctrl+Alt+H` |
| Todas las anotaciones | `Ctrl+Alt+N` |
| Elegir tema | `Ctrl+Shift+T` |
| Salir | `Ctrl+Q` |

Dentro del lector, `Ctrl+F` busca en el libro, `Ctrl+M` guarda un marcador, `Ctrl+H` resalta una selección, `Ctrl+Shift+N` añade una nota y `Ctrl+T` alterna el panel lateral. `Page Up` y `Page Down` cambian la página o sección; `F11` y `Esc` controlan la pantalla completa.

## Datos

El catálogo, el índice de texto y las anotaciones están en `.arandu/library.sqlite3`. La carpeta guarda también los temas (`themes.json`), ajustes de lectura (`reader.json`), preferencias de ventana (`window.json`), portadas (`covers/`) y copias automáticas (`backups/`). Los catálogos anteriores se migran conservando sus libros, notas, favoritos y progreso. Los archivos de `.arandu/imports/` siguen disponibles.

La aplicación trabaja directamente con archivos y SQLite. Los capítulos EPUB se presentan como documentos de texto enriquecido de Qt: no ejecutan JavaScript y sus recursos solo se resuelven dentro del EPUB abierto. La aplicación no utiliza servicios externos ni telemetría.

## Copias de seguridad

**Datos → Crear copia de seguridad…** genera un ZIP verificado del catálogo, anotaciones, progreso, etiquetas, categorías, colecciones, estantes inteligentes, reglas, ediciones, libros ocultos, exclusiones de archivos y carpetas, papelera, estadísticas, metas, texto OCR, temas y ajustes. Las portadas personalizadas se incluyen siempre. Podés incluir también todos los PDF y EPUB disponibles, incluidos los de la papelera, ocultos o apartados por exclusiones, para crear una copia portable. Las copias de contenido idéntico se guardan una sola vez dentro del ZIP.

**Datos → Restaurar copia…** verifica el catálogo y la integridad de los libros incluidos. Antes de sustituir el estado actual, guarda otra copia en `.arandu/backups/antes-de-restaurar-…zip`. Los originales se conservan. Si faltan archivos, los libros incluidos se recuperan dentro de `.arandu/imports/recovered/`, conservando sus fichas y anotaciones, incluso cuando hay duplicados. Las portadas y el índice se actualizan después de restaurar.

Al cerrar Arandu se crea una copia automática diaria del catálogo y sus preferencias en `.arandu/backups/`. Estas copias automáticas no incluyen los archivos originales: para respaldarlos, elegí incluir libros al crear una copia manual. Como alternativa, con la app cerrada podés copiar `.arandu/` y respaldar por separado las carpetas conectadas.

## Verificación

```sh
python3 -m unittest discover -s tests -v
```

Las pruebas Qt usan la plataforma `offscreen`: no abren ventanas en tu escritorio. Cubren importación, migraciones, metadatos, portadas, búsqueda de texto, duplicados, archivos movidos, vigilancia real de carpetas, fuentes parciales, exclusiones sin recorrido, libros ocultos, colecciones, PDF y EPUB, selección, anotaciones, lectura, temas, contraste, ventanas pequeñas y restauración portable. Las pruebas PDF requieren Poppler.

- `arandu.py`: inicio de la aplicación.
- `desktop.py`: ventana, catálogo y diálogos nativos.
- `readers.py`: lectores integrados.
- `paged_text.py`: páginas EPUB con selección y enlaces nativos.
- `book_navigation.py`: índice EPUB jerárquico y miniaturas PDF bajo demanda.
- `history.py`: historial transaccional, deshacer/rehacer y posición exacta de lectura.
- `library_review.py`: consulta global de anotaciones, diagnóstico y reparación por lotes.
- `review_dialogs.py`: ventanas de historial, anotaciones globales y diagnóstico.
- `library.py`: catálogo SQLite, metadatos y escaneo.
- `themes.py`: paletas, estilos y carga de Montserrat y Karla.
- `theme_editor.py`: editor nativo de temas personalizados.
- `branding.py`: logo e iconos adaptados al tema.
- `extraction.py`: metadatos, portadas y extracción de texto con Poppler y EPUB.
- `pdf_page.py`: selección y resaltados sobre las páginas PDF.
- `reading_tools.py`: ajustes de lectura y gestión de anotaciones.
- `library_dialogs.py`: búsqueda, colecciones y revisión de duplicados.
- `taxonomy.py`: edición, filtros y gestión de etiquetas y categorías.
- `organizer.py`: edición por lotes, estantes, reglas, ediciones, papelera, estadísticas y exportación.
- `sources.py`: fuentes, exclusiones recursivas y visibilidad de fichas.
- `source_dialogs.py`: revisión de carpetas, archivos ignorados y libros ocultos.
- `organization_dialogs.py`: herramientas nativas de organización y objetivos.
- `activity.py`: registro de sesiones y pausa por inactividad.
- `cover_tools.py`: normalización de portadas propias.
- `ocr.py`: Tesseract local y palabras seleccionables de PDF escaneados.
- `theme_manager.py`: intercambio de temas y tamaño del texto.
- `backups.py`: copias verificadas y restauración con copia previa.
- `assets/branding/`: logo original, vectores y versiones de las seis paletas.
- `assets/fonts/`: tipografías incluidas y sus licencias.
