# Analizador Automático de Artículos (PDF a LaTeX)

Este proyecto es una herramienta en Python diseñada para automatizar la lectura, análisis y síntesis de artículos científicos en formato PDF utilizando la API de **Gemini** de Google. Genera reportes estructurados directamente en formato **LaTeX**, listos para compilar a PDF.

## Características

*   **Análisis con IA (Gemini)**: Extrae el resumen, objetivos, metodología, resultados, figuras relevantes y conclusiones de cada PDF.
*   **Generación automática de Reportes LaTeX**: Genera un archivo `.tex` consolidado y, si tienes `pdflatex` instalado, lo compila a `.pdf` automáticamente.
*   **Extracción de Imágenes**: Extrae las imágenes del PDF original para incorporarlas como figuras en el reporte final.
*   **Sistema de Caché Inteligente**: Calcula el hash MD5 de los archivos. Si un PDF no ha cambiado, no vuelve a consumir créditos de la API y lee el análisis desde la caché.
*   **Fácil de Ejecutar**: Admite argumentos de consola para especificar rutas de entrada, salida y modo simulación (*mock*).

---

## Requisitos Previos

Antes de ejecutar el script, asegúrate de tener instalado:

1.  **Python 3.8 o superior**
2.  (Opcional) Una distribución de LaTeX como **TeX Live** o **MiKTeX** (para que el script pueda compilar el PDF de salida automáticamente con el comando `pdflatex`).

---

## Instalación y Configuración

Sigue estos pasos para poner en marcha el proyecto en tu máquina local:

### 1. Clonar el repositorio
```bash
git clone https://github.com/sebas30020/analizador_articulos.git
cd analizador_articulos
```

### 2. Crear e iniciar un entorno virtual (opcional pero recomendado)
En Windows:
```bash
python -m venv .venv
.venv\Scripts\activate
```
En macOS/Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Instalar las dependencias
Instala los paquetes necesarios desde el archivo `requirements.txt`:
```bash
pip install -r requirements.txt
```

*Nota: Para habilitar la extracción automática de imágenes desde los PDFs, se recomienda instalar `PyMuPDF` (también conocido como `fitz`):*
```bash
pip install PyMuPDF
```

### 4. Configurar tu API Key de Gemini
1.  Haz una copia del archivo `.env.template` y renómbrala a `.env` en la raíz del proyecto.
2.  Abre el archivo `.env` con un editor de texto y coloca tu clave de API de Gemini:
    ```env
    GEMINI_API_KEY=tu_clave_api_de_gemini_aqui
    ```

> 💡 Puedes obtener una clave de API gratuita en el portal [Google AI Studio](https://aistudio.google.com/).

---

## Uso

### Ejecución básica
Para ejecutar la aplicación de forma predeterminada, coloca tus archivos PDF dentro de la carpeta `pdfs/` y ejecuta:
```bash
python analizar.py
```

### Opciones de línea de comandos (Argumentos)
Puedes modificar el comportamiento del script mediante parámetros:

| Parámetro | Descripción |
| :--- | :--- |
| `-i`, `--input` | Ruta al directorio con los archivos PDF a analizar. |
| `-o`, `--output` | Ruta al directorio donde guardar los resultados y reportes. |
| `--mock` | Ejecuta el script en modo simulación (sin hacer llamadas reales a la API de Gemini). |

**Ejemplos:**
```bash
# Cambiar carpeta de entrada y salida
python analizar.py -i /ruta/mis_pdfs -o /ruta/salida

# Ejecutar una simulación rápida (modo mock) para verificar el flujo de trabajo
python analizar.py --mock
```

---

## Estructura de Salida

Una vez finalizado el script, el directorio de salida (por defecto, `resultados/`) contendrá:
*   `reporte_articulos.tex`: El código fuente en LaTeX consolidado con los análisis de todos los PDFs.
*   `reporte_articulos.pdf`: El reporte compilado (si `pdflatex` está disponible).
*   `summaries/`:
    *   `registry.json`: Registro interno de los archivos analizados con sus hashes MD5 correspondientes.
    *   Archivos individuales `.tex` con la síntesis detallada de cada artículo.
*   `images/`: Figuras extraídas de los PDFs para ser referenciadas dentro del reporte.
