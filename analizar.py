import os
import sys
import hashlib
import json
import time
import re
import subprocess
import argparse
from pathlib import Path
from google import genai
from google.genai import errors
from dotenv import load_dotenv

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# Configuración de rutas
BASE_DIR = Path(__file__).resolve().parent
PDFS_DIR = BASE_DIR / "pdfs"
SUMMARIES_DIR = BASE_DIR / "summaries"
IMAGES_DIR = BASE_DIR / "images"
REGISTRY_FILE = SUMMARIES_DIR / "registry.json"
OUTPUT_TEX_FILE = BASE_DIR / "reporte_articulos.tex"

# Cargar variables de entorno desde .env
load_dotenv(BASE_DIR / ".env")

def get_gemini_client():
    """Inicializa y retorna el cliente de Gemini API."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: No se encontró la API Key de Gemini.")
        print("Por favor, crea un archivo '.env' en la raíz del proyecto y define:")
        print("GEMINI_API_KEY=tu_api_key")
        print("\nO configura la variable de entorno GEMINI_API_KEY.")
        sys.exit(1)
    
    return genai.Client(api_key=api_key)

def calculate_md5(filepath):
    """Calcula el hash MD5 de un archivo para control de cambios."""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def load_registry():
    """Carga el registro de archivos analizados."""
    if REGISTRY_FILE.exists():
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Advertencia: No se pudo leer el registro ({e}). Se creará uno nuevo.")
    return {}

def save_registry(registry):
    """Guarda el registro de archivos analizados."""
    SUMMARIES_DIR.mkdir(exist_ok=True)
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=4, ensure_ascii=False)

def escape_latex(text):
    """Escapa caracteres especiales de LaTeX para evitar errores de compilación."""
    latex_special = {
        '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
        '_': r'\_', '{': r'\{', '}': r'\}',
        '~': r'\textasciitilde{}', '^': r'\textasciicircum{}',
    }
    text = text.replace('\\', r'\textbackslash{}')
    res = ""
    for char in text:
        if char in latex_special:
            res += latex_special[char]
        else:
            res += char
    return res

def clean_markdown_latex(text):
    """Remueve bloques de código markdown si el modelo los incluye."""
    text = text.strip()
    if text.startswith("```latex"):
        text = text[len("```latex"):].strip()
    elif text.startswith("```"):
        text = text[3:].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    
    # Evita que LaTeX interprete [ en una nueva línea (ej. [9]) como argumento opcional de \\ o \midrule
    text = re.sub(r'(?m)^\[', '{[}', text)
    return text

def heal_latex_images(latex_text, images_dir):
    """Post-procesa el LaTeX para curar extensiones erróneas u omitir imágenes no encontradas."""
    figure_pattern = re.compile(r"\\begin\{figure\}(?:\[.*?\])?(.*?)\\end\{figure\}", re.DOTALL)
    include_pattern = re.compile(r"\\includegraphics(?:\[.*?\])?\{(.*?)\}")
    
    def replace_figure(match):
        full_figure_block = match.group(0)
        figure_content = match.group(1)
        
        img_match = include_pattern.search(figure_content)
        if not img_match:
            return full_figure_block
            
        img_path_str = img_match.group(1)
        img_path = Path(img_path_str)
        filename = img_path.name
        
        actual_path = images_dir / filename
        if actual_path.exists():
            return full_figure_block
            
        stem = img_path.stem
        found_file = None
        for ext in ['.png', '.jpeg', '.jpg', '.pdf']:
            possible_path = images_dir / f"{stem}{ext}"
            if possible_path.exists():
                found_file = possible_path
                break
                
        if found_file:
            new_img_path_str = str(img_path.parent / found_file.name).replace('\\', '/')
            new_figure_content = figure_content.replace(img_path_str, new_img_path_str)
            prefix = full_figure_block[:full_figure_block.find(figure_content)]
            suffix = full_figure_block[full_figure_block.find(figure_content) + len(figure_content):]
            print(f"[Curador de LaTeX] Extensión corregida: {img_path_str} -> {new_img_path_str}")
            return prefix + new_figure_content + suffix
        else:
            print(f"[Curador de LaTeX] Figura omitida (no existe el archivo en disco): {img_path_str}")
            return f"% [Imagen omitida porque no se encontró el archivo: {filename}]"
            
    return figure_pattern.sub(replace_figure, latex_text)

def extract_images_from_pdf(pdf_path, file_hash):
    """Extrae imágenes del PDF usando PyMuPDF y las guarda localmente."""
    if not fitz:
        print("Advertencia: PyMuPDF (fitz) no está instalado. No se extraerán imágenes.")
        return []

    doc = fitz.open(pdf_path)
    extracted_images = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        image_list = page.get_images(full=True)
        
        for img_index, img in enumerate(image_list, start=1):
            xref = img[0]
            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                image_ext = base_image["ext"]
                width = base_image["width"]
                height = base_image["height"]
                
                # Filtro: ignorar imágenes pequeñas (íconos, logos)
                if width < 200 or height < 200 or len(image_bytes) < 5000:
                    continue
                    
                image_name = f"{file_hash}_p{page_num+1}_i{img_index}.{image_ext}"
                image_path = IMAGES_DIR / image_name
                
                with open(image_path, "wb") as f:
                    f.write(image_bytes)
                    
                extracted_images.append(image_path)
            except Exception as e:
                print(f"Error extrayendo imagen en página {page_num+1}: {e}")
                
    return extracted_images

def analyze_pdf(client, pdf_path, file_hash, model_name="gemini-2.5-flash", mock_mode=False):
    """Sube el PDF y sus imágenes a Gemini, y realiza el análisis."""
    if mock_mode:
        print(f"[Mock] Simulando análisis de '{pdf_path.name}'...")
        time.sleep(1)
        return "\\section{Título Simulado del Paper}\nEste es un resumen de prueba..."

    print(f"Extrayendo imágenes de {pdf_path.name}...")
    local_images = extract_images_from_pdf(pdf_path, file_hash)
    print(f"Se extrajeron {len(local_images)} imágenes.")

    print(f"Subiendo archivo a Gemini API: {pdf_path.name}...")
    uploaded_files = []
    
    try:
        pdf_file = client.files.upload(file=pdf_path)
        uploaded_files.append(pdf_file)
        print(f"PDF subido. ID: {pdf_file.name}. Procesando...")
        
        # Esperar procesamiento del PDF
        wait_time = 0
        while pdf_file.state.name == "PROCESSING":
            time.sleep(2)
            wait_time += 2
            pdf_file = client.files.get(name=pdf_file.name)
            
        if pdf_file.state.name == "FAILED":
            raise RuntimeError(f"Fallo en Gemini al procesar PDF: {pdf_file.error.message}")

        # Subir las imágenes extraídas a Gemini
        uploaded_images_info = []
        for img_path in local_images:
            img_file = client.files.upload(file=img_path)
            uploaded_files.append(img_file)
            uploaded_images_info.append(f"Archivo local: {img_path.name}")
            
        # Preparar lista de archivos para el prompt
        contents = [pdf_file] + [f for f in uploaded_files if f != pdf_file]

        prompt = (
            "Quiero que realices un resumen analítico completo y organizado del texto científico proporcionado.\n\n"
            "Instrucciones clave:\n"
            "1. INICIA tu respuesta identificando el TÍTULO REAL y completo del artículo científico y colócalo como el nivel más alto de sección usando: \\section{TÍTULO REAL DEL ARTÍCULO}\n"
            "2. No omitas información relevante. Usa listas o tablas si es necesario.\n"
            "3. Divide el resumen en las siguientes subsecciones usando \\subsection{...}:\n"
            "   - Contexto y objetivo\n"
            "   - Metodología o desarrollo\n"
            "   - Resultados o hallazgos\n"
            "   - Discusión o análisis crítico\n"
            "   - Conclusiones o implicaciones\n"
            "   - Síntesis final (máximo 5 líneas)\n"
            "4. IMÁGENES: He adjuntado al prompt varias imágenes extraídas del PDF original.\n"
            "   - Revisa el PDF, identifica qué imágenes o figuras son las más representativas e importantes, y asócialas con las imágenes de la lista adjunta.\n"
            "   - Está ESTRICTAMENTE PROHIBIDO usar o inventar nombres de archivo que no aparezcan en la lista 'Nombres de las imágenes extraídas disponibles'.\n"
            "   - Si una figura del artículo es importante pero NO está en la lista de imágenes extraídas disponibles (por ejemplo, porque es un gráfico vectorial y no se extrajo como imagen), NO debes incluirla con \\includegraphics. Puedes hacer referencia a ella en el texto, pero sin el bloque \\begin{figure}...\\end{figure}.\n"
            "   - Para insertar una imagen de la lista, usa EXACTAMENTE este formato, respetando la extensión exacta (png, jpeg, etc.) indicada en la lista:\n"
            "     \\begin{figure}[H]\n"
            "     \\centering\n"
            "     \\includegraphics[width=0.8\\textwidth]{images/NOMBRE_ARCHIVO_AQUI}\n"
            "     \\caption{Descripción de la imagen según el documento}\n"
            "     \\label{fig:NOMBRE_ARCHIVO_AQUI}\n"
            "     \\end{figure}\n"
            "   - Cuando te refieras a una de estas figuras en el texto, utiliza obligatoriamente la referencia de LaTeX: \\ref{fig:NOMBRE_ARCHIVO_AQUI}.\n\n"
            f"Nombres de las imágenes extraídas disponibles:\n" + "\n".join(uploaded_images_info) + "\n\n"
            "--- INSTRUCCIÓN DE FORMATO LATEX ---\n"
            "Tu respuesta debe ser EXCLUSIVAMENTE código LaTeX válido, listo para integrarse en un documento general.\n"
            "No incluyas \\documentclass ni \\begin{document} ni \\end{document}.\n"
            "Escapa caracteres especiales de LaTeX (%, &, _, etc.).\n"
            "No uses negritas de markdown (**texto**), usa \\textbf{texto}.\n"
        )
        
        contents.insert(0, prompt)
        print("Enviando prompt de análisis a Gemini...")
        response = client.models.generate_content(
            model=model_name,
            contents=contents
        )
        
        print(f"DEBUG Response: {repr(response.text)}")
        cleaned = clean_markdown_latex(response.text)
        print(f"DEBUG Cleaned: {repr(cleaned)}")
        return cleaned
        
    finally:
        # Limpieza de archivos en Gemini
        for f in uploaded_files:
            try:
                client.files.delete(name=f.name)
            except Exception:
                pass

def generate_full_report(registry, active_pdfs):
    """Regenera el archivo principal reporte_articulos.tex y lo compila."""
    print("Regenerando reporte LaTeX general...")
    
    preamble = (
        "\\documentclass[11pt,a4paper]{article}\n"
        "\\usepackage[utf8]{inputenc}\n"
        "\\usepackage[spanish]{babel}\n"
        "\\usepackage{geometry}\n"
        "\\geometry{margin=1in}\n"
        "\\usepackage{booktabs}\n"
        "\\usepackage{longtable}\n"
        "\\usepackage{graphicx}\n"
        "\\usepackage{float}\n"
        "\\usepackage{hyperref}\n"
        "\\usepackage{amsmath}\n"
        "\\usepackage{amssymb}\n"
        "\\usepackage{array}\n\n"
        "\\title{Reporte y Resumen Analítico de Artículos Científicos}\n"
        "\\date{\\today}\n\n"
        "\\begin{document}\n\n"
        "\\maketitle\n"
        "\\tableofcontents\n"
        "\\newpage\n\n"
    )
    
    body_content = ""
    for pdf_filename in sorted(active_pdfs):
        registry_entry = registry.get(pdf_filename)
        if not registry_entry:
            continue
            
        summary_file = SUMMARIES_DIR / registry_entry["summary_file"]
        if not summary_file.exists():
            continue
            
        with open(summary_file, "r", encoding="utf-8") as f:
            summary_text = f.read()
            
        summary_text = heal_latex_images(summary_text, IMAGES_DIR)
            
        body_content += f"\\newpage\n"
        body_content += f"% --- Inicio del documento: {escape_latex(pdf_filename)} ---\n"
        # El título real (\section{...}) es generado ahora por Gemini dentro de summary_text
        body_content += f"{summary_text}\n\n"
        
    postamble = "\\end{document}\n"
    
    with open(OUTPUT_TEX_FILE, "w", encoding="utf-8") as f:
        f.write(preamble + body_content + postamble)
        
    print(f"Reporte LaTeX guardado en: {OUTPUT_TEX_FILE}")
    
    # Compilación automática
    print("Iniciando compilación del PDF con pdflatex...")
    try:
        # Primera compilación para estructura
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", OUTPUT_TEX_FILE.name],
            cwd=OUTPUT_TEX_FILE.parent, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        # Segunda compilación para el índice (TOC) y referencias
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", OUTPUT_TEX_FILE.name],
            cwd=OUTPUT_TEX_FILE.parent, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print("¡Compilación finalizada! El archivo 'reporte_articulos.pdf' ha sido generado exitosamente.")
    except FileNotFoundError:
        print("Advertencia: No se encontró 'pdflatex' en el PATH. No se pudo compilar el PDF automáticamente.")
    except subprocess.CalledProcessError:
        print("Hubo un error al compilar el documento LaTeX. Revisa 'reporte_articulos.log' para más detalles.")

def main():
    global PDFS_DIR, SUMMARIES_DIR, IMAGES_DIR, REGISTRY_FILE, OUTPUT_TEX_FILE
    
    parser = argparse.ArgumentParser(description="Analizador Automático de Papers (PDF a LaTeX)")
    parser.add_argument("-i", "--input", type=str, help="Directorio con los archivos PDF a analizar")
    parser.add_argument("-o", "--output", type=str, help="Directorio donde guardar los resultados y reportes")
    parser.add_argument("--mock", action="store_true", help="Activar modo simulación (mock) para pruebas")
    args = parser.parse_args()

    if args.input:
        PDFS_DIR = Path(args.input).resolve()
        
    if args.output:
        output_dir = Path(args.output).resolve()
    else:
        output_dir = BASE_DIR / "resultados"
        
    SUMMARIES_DIR = output_dir / "summaries"
    IMAGES_DIR = output_dir / "images"
    REGISTRY_FILE = SUMMARIES_DIR / "registry.json"
    OUTPUT_TEX_FILE = output_dir / "reporte_articulos.tex"

    mock_mode = args.mock
    if mock_mode:
        print("=== MODO SIMULACIÓN (MOCK) ACTIVADO ===")
    else:
        print("=== ANALIZADOR AUTOMÁTICO DE PAPERS (PDF A LATEX) ===")
    
    PDFS_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    
    pdf_files = sorted(list(set(PDFS_DIR.glob("*.pdf")) | set(PDFS_DIR.glob("*.PDF"))))
    if not pdf_files:
        print(f"\nNo se encontraron PDFs en {PDFS_DIR}")
        generate_full_report({}, [])
        return
        
    registry = load_registry()
    client = None
    new_analyzed = False
    active_pdf_names = []
    
    for pdf_path in pdf_files:
        pdf_filename = pdf_path.name
        active_pdf_names.append(pdf_filename)
        file_hash = calculate_md5(pdf_path)
        
        if pdf_filename in registry and registry[pdf_filename]["hash"] == file_hash:
            summary_path = SUMMARIES_DIR / registry[pdf_filename]["summary_file"]
            if summary_path.exists():
                print(f"[Caché] '{pdf_filename}' ya analizado.")
                continue
        
        if client is None and not mock_mode:
            client = get_gemini_client()
            
        print(f"\n[Analizando] '{pdf_filename}'")
        try:
            latex_summary = analyze_pdf(client, pdf_path, file_hash, mock_mode=mock_mode)
            
            safe_name = "".join(c for c in pdf_filename if c.isalnum() or c in (".", "_", "-")).rstrip(".")
            fragment_filename = f"{file_hash}_{safe_name}.tex"
            fragment_path = SUMMARIES_DIR / fragment_filename
            
            latex_summary = heal_latex_images(latex_summary, IMAGES_DIR)
            
            with open(fragment_path, "w", encoding="utf-8") as f:
                f.write(latex_summary)
                
            registry[pdf_filename] = {
                "hash": file_hash,
                "summary_file": fragment_filename,
                "analyzed_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            save_registry(registry)
            new_analyzed = True
            
        except Exception as e:
            print(f"Error procesando '{pdf_filename}': {e}")
            
    generate_full_report(registry, active_pdf_names)
    print("\nProceso finalizado.")

if __name__ == "__main__":
    main()
