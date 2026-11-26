# Logo de Arandu

El símbolo y las letras proceden de `reference.png`, la imagen suministrada por el usuario. El nombre de la aplicación es **Arandu**, sin acento.

Los SVG conservan la silueta original, incluido el libro abierto dentro de la A y las letras del nombre. La conversión elimina el fondo blanco y los márgenes de la imagen de referencia.

- `logo.svg`: composición horizontal para la barra lateral.
- `mark.svg`: símbolo para la navegación compacta.
- `icon.svg`: símbolo con fondo propio para el icono de ventana y de la aplicación.
- `stacked.svg`: composición vertical de la referencia original.
- `themes/`: versiones del logo, símbolo e icono en las seis paletas.

El símbolo toma el color de acento; las letras toman el color principal de texto. El icono usa el fondo del tema, de modo que su símbolo conserve contraste en el escritorio. La aplicación los adapta al cambiar de tema.

Para regenerar los vectores desde la referencia (Pillow, solo para desarrollo):

```sh
python3 tools/vectorize_logo.py assets/branding/reference.png
```

Para regenerar las versiones de las seis paletas:

```sh
python3 tools/export_branding.py
```
