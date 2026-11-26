# Modelos OCR incluidos

Arandu incluye los modelos LSTM de español (`spa.traineddata`) e inglés (`eng.traineddata`) del proyecto oficial [tesseract-ocr/tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast). Licencia Apache 2.0: ver [LICENSE](LICENSE).

Fuentes de los archivos, descargados el 25 de noviembre de 2026:

- https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/spa.traineddata
- https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata

El programa usa `--tessdata-dir` para consultar estos archivos locales. Requiere el motor Tesseract instalado y no descarga datos durante el reconocimiento. La salida TSV incluye las coordenadas de palabras que Arandu conserva en SQLite para seleccionar texto sobre la imagen de cada página.
