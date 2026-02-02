import os
import time
import glob
import base64
import requests
import pdfplumber
import urllib3
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from openai import OpenAI

# --- CONFIGURAÇÕES ---
try:
    from dotenv import load_dotenv
    load_dotenv("Chaves.env")
except:
    pass

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    print("❌ ERRO: Chaves de API não configuradas nos Secrets!")
    exit(1)

client = OpenAI(api_key=OPENAI_API_KEY)
urllib3.disable_warnings()

# --- 1. CONFIGURAÇÃO DO DRIVER (Modo Nuvem) ---
def configurar_driver():
    chrome_options = Options()
    
    # OBRIGATÓRIO PARA GITHUB ACTIONS
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--ignore-certificate-errors")
    
    pasta_download = os.getcwd()
    prefs = {
        "download.default_directory": pasta_download,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    print(f"📂 Pasta de Download configurada: {pasta_download}")
    
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)

# --- 2. CAÇADOR DE ARQUIVOS ---
def esperar_e_renomear(pasta, timeout=90):
    print("👀 Vigiando pasta por novos arquivos...")
    fim = time.time() + timeout
    
    while time.time() < fim:
        arquivos = glob.glob(os.path.join(pasta, "*.pdf"))
        if arquivos:
            recente = max(arquivos, key=os.path.getmtime)
            if "diario_hoje.pdf" in recente: # Ignora o próprio arquivo se já existir
                if time.time() - os.path.getmtime(recente) > 60:
                    pass # É velho
                else:
                    return recente # É o novo renomeado

            if ".crdownload" not in recente:
                print(f"✅ Arquivo capturado: {os.path.basename(recente)}")
                novo_nome = os.path.join(pasta, "diario_hoje.pdf")
                if os.path.exists(novo_nome): os.remove(novo_nome)
                os.rename(recente, novo_nome)
                return novo_nome
        
        time.sleep(1)
    return None

# --- 3. ROBÔ DE DOWNLOAD ---
def buscar_diario():
    url_portal = "https://atos.teresopolis.rj.gov.br/diario/"
    driver = None
    
    try:
        driver = configurar_driver()
        driver.set_page_load_timeout(120)
        
        print(f"🕵️  Acessando portal...")
        driver.get(url_portal)
        
        wait = WebDriverWait(driver, 30)
        print("⏳ Aguardando lista...")
        xpath_linha = "//*[contains(text(), 'Edição') and contains(text(), 'Ano')]"
        wait.until(EC.presence_of_element_located((By.XPATH, xpath_linha)))
        elementos = driver.find_elements(By.XPATH, xpath_linha)
        
        alvo = None
        for elem in elementos:
            if "2026" in elem.text:
                alvo = elem
                break
        if not alvo and elementos: alvo = elementos[0]

        if alvo:
            print(f"🎯 Alvo: '{alvo.text}'")
            driver.execute_script("arguments[0].click();", alvo)
            print("⏳ Aguardando visualizador (15s)...")
            time.sleep(15) 
            
            print("👇 Tentando clicar no botão de Download...")
            driver.execute_script("""
                var btn = document.querySelector('a[download]') || 
                          document.querySelector('button[title="Download"]') ||
                          document.querySelector('#download');
                if(btn) btn.click();
            """)
            
            pdf_final = esperar_e_renomear(os.getcwd())
            if pdf_final:
                return pdf_final, driver.current_url
            else:
                print("❌ Timeout: O arquivo não apareceu.")
        else:
            print("❌ Nenhuma edição encontrada.")

        return None, None

    except Exception as e:
        print(f"❌ Erro Crítico: {e}")
        return None, None
    finally:
        if driver: driver.quit()

# --- 4. EXTRATOR ---
def extrair_texto(caminho):
    print("📖 Extraindo texto...")
    try:
        text = ""
        with pdfplumber.open(caminho) as pdf:
            for page in pdf.pages[:25]: 
                text += page.extract_text() or ""
        return text[:100000]
    except: return ""

# --- 5. IA ---
def analisar(texto):
    print("🧠 Analisando...")
    prompt = """
    Analise o Diário Oficial de Teresópolis.
    Busque: Licitações, Contratos, Pregões.
    Ignore: RH, Férias.
    Formato Markdown:
    🚨 **[TIPO]** Resumo
    💰 **Valor:** R$ X
    Se nada: "ND"
    """
    try:
        return client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": texto}],
            temperature=0.3
        ).choices[0].message.content
    except: return "ND"

# --- 6. TELEGRAM ---
def enviar_telegram(msg, link):
    print("📲 Enviando Telegram...")
    data = time.strftime("%d/%m")
    texto = f"📊 *Monitor Teresópolis* ({data})\n🚀 *Oportunidades!*\n\n{msg}\n\n🔗 [Link]({link})" if (msg and "ND" not in msg) else f"📊 *Monitor Teresópolis* ({data})\nℹ️ Nada novo hoje.\n🔗 [Link]({link})"
        
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
            "chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "Markdown", "disable_web_page_preview": True
        })
        print("✅ Enviado!")
    except: pass

if __name__ == "__main__":
    pdf, link = buscar_diario()
    if pdf:
        enviar_telegram(analisar(extrair_texto(pdf)), link)
        print("🏁 SUCESSO.")
    else:
        print("❌ Falha.")
