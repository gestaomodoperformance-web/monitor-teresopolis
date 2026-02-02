import os
import time
import requests
import pdfplumber
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from openai import OpenAI

# --- CONFIGURAÇÕES ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# --- 1. DRIVER BLINDADO ---
def configurar_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    try:
        caminho = ChromeDriverManager().install()
        if "THIRD_PARTY_NOTICES" in caminho:
            pasta = os.path.dirname(caminho)
            caminho = os.path.join(pasta, "chromedriver")
        os.chmod(caminho, 0o755)
        service = Service(executable_path=caminho)
        return webdriver.Chrome(service=service, options=chrome_options)
    except:
        return webdriver.Chrome(options=chrome_options)

# --- 2. SCRAPER COM "RAIO-X" ---
def buscar_e_baixar_diario():
    url = "https://atos.teresopolis.rj.gov.br/diario/"
    caminho_pdf = "/tmp/diario_hoje.pdf" if os.name != 'nt' else "diario_hoje.pdf"
    driver = None
    
    print(f"🕵️  Acessando: {url}")
    
    try:
        driver = configurar_driver()
        driver.set_page_load_timeout(60)
        driver.get(url)
        
        print(f"📡 Título: {driver.title}")
        time.sleep(15) # Espera GIGANTE para garantir que o Ionic carregou
        
        # TENTATIVA 1: Busca por Ícones FontAwesome (Padrão Atos/Mentor)
        print("🔍 Tentativa 1: Procurando ícones de PDF...")
        try:
            # Procura qualquer coisa que pareça um arquivo ou download
            # fa-file-pdf, fa-download, fa-eye
            xpath_icone = "//i[contains(@class, 'fa-file') or contains(@class, 'fa-download') or contains(@class, 'fa-eye')]"
            icones = driver.find_elements(By.XPATH, xpath_icone)
            
            if icones:
                print(f"✨ Encontrados {len(icones)} ícones. Clicando no primeiro...")
                botao = icones[0]
                # Clica no PAI do ícone (geralmente o botão)
                driver.execute_script("arguments[0].parentNode.click();", botao)
                time.sleep(10)
                
                # Verifica abas
                if len(driver.window_handles) > 1:
                    driver.switch_to.window(driver.window_handles[-1])
                
                link = driver.current_url
                print(f"🔗 Link capturado: {link}")
                
                # Download
                resp = requests.get(link, stream=True)
                if resp.status_code == 200:
                    with open(caminho_pdf, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    return caminho_pdf, link
        except Exception as e:
            print(f"⚠️ Falha na busca por ícones: {e}")

        # SE FALHAR TUDO: MODALIDADE RAIO-X
        print("❌ Não achei o botão. Iniciando RAIO-X da página...")
        print("--- INÍCIO DO HTML (Copie isso se der erro) ---")
        html = driver.page_source
        # Imprime os primeiros 3000 caracteres para não poluir demais, mas mostrar a estrutura
        print(html[:4000]) 
        print("--- FIM DO HTML ---")
        
        return None, None

    except Exception as e:
        print(f"❌ ERRO GERAL: {e}")
        return None, None
    finally:
        if driver:
            driver.quit()

# --- 3. EXTRATOR ---
def extrair_texto(caminho):
    try:
        text = ""
        with pdfplumber.open(caminho) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        return text[:100000]
    except:
        return ""

# --- 4. IA ---
def analisar(texto):
    print("🧠 Analisando...")
    prompt = """
    Analise o texto. Busque: Licitações, Pregões, Chamamentos.
    Se encontrar: 🚨 [Nicho] | 📦 Objeto | 💰 Valor.
    Se nada: "ND"
    """
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": texto}],
            temperature=0.3
        )
        return resp.choices[0].message.content
    except:
        return "ND"

# --- 5. TELEGRAM ---
def enviar_telegram(msg, link):
    print("📲 Enviando...")
    texto = f"📊 *Monitor Teresópolis*\nℹ️ Sem oportunidades hoje.\n🔗 [Link]({link})"
    if msg and "ND" not in msg:
        texto = f"📊 *Monitor Teresópolis*\n🚀 *Oportunidades!*\n\n{msg}\n\n🔗 [Link]({link})"
        
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    })

def main():
    pdf, link = buscar_e_baixar_diario()
    if pdf and link:
        texto = extrair_texto(pdf)
        resumo = analisar(texto)
        enviar_telegram(resumo, link)
        print("✅ FIM.")
    else:
        print("❌ FALHA NO DOWNLOAD.")

if __name__ == "__main__":
    main()
