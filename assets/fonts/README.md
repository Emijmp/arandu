# Tipografías de Arandu

La interfaz usa **Montserrat** para encabezados e índice y **Karla** para texto y controles. Las fuentes están incluidas para funcionar sin conexión y sin instalarlas en el sistema.

Fuentes originales del repositorio oficial Google Fonts:

- [Montserrat](https://github.com/google/fonts/tree/main/ofl/montserrat)
- [Karla](https://github.com/google/fonts/tree/main/ofl/karla)

Los archivos `Montserrat.ttf` y `Karla.ttf` son las versiones variables originales. Los archivos `Regular`, `SemiBold` y `Bold` son instancias estáticas de los pesos 400, 600 y 700, generadas con `fontTools.varLib.instancer.instantiateVariableFont(..., updateFontNames=True)` para que Qt 5 seleccione correctamente sus pesos. La aplicación carga esas seis instancias estáticas; fontTools no es una dependencia de ejecución.

Las licencias SIL Open Font License 1.1 se conservan en `Montserrat-OFL.txt` y `Karla-OFL.txt`.
