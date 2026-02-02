import os
import time
import requests
import pdfplumber
import logging
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

# --- 1. CONFIGURAÇÃO "TANQUE DE GUERRA" DO DRIVER ---
def configurar_driver():
    chrome_options = Options()
    # Modo Headless Novo (Mais estável)
    chrome_options.add_argument("--headless=new") 
    
    # Lista de flags para evitar crash em servidor Linux
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--ignore-certificate-errors")
    
    # User-Agent comum para não parecer robô
    chrome_options.add_argument("user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    print("🚗 Configurando Driver Blindado...")
    try:
        # Tenta instalação padrão
        caminho = ChromeDriverManager().install()
        
        # Correção do bug de caminho do Linux (Third Party Notices)
        if "THIRD_PARTY_NOTICES" in caminho:
            pasta = os.path.dirname(caminho)
            caminho = os.path.join(pasta, "chromedriver")
        
        # Força permissão de execução
        os.chmod(caminho, 0o755)
        
        service = Service(executable_path=caminho)
        return webdriver.Chrome(service=service, options=chrome_options)
    except Exception as e:
        print(f"⚠️ Erro no Manager: {e}. Tentando driver nativo...")
        return webdriver.Chrome(options=chrome_options)

# --- 2. SCRAPER ---
def buscar_e_baixar_diario():
    url_sistema = "https://atos.teresopolis.rj.gov.br/diario/"
    
    if os.name == 'nt':
        caminho_pdf = "diario_hoje.pdf"
    else:
        caminho_pdf = "/tmp/diario_hoje.pdf"
        
    driver = None
    
    print(f"🕵️  Acessando: {url_sistema}")
    
    try:
        driver = configurar_driver()
        driver.set_page_load_timeout(60) # Limite de 60s para carregar
        driver.get(url_sistema)
        
        # --- DIAGNÓSTICO ---
        print(f"📡 Título da Página capturado: {driver.title}")
        # -------------------

        wait = WebDriverWait(driver, 30)
        
        print("⏳ Aguardando tabela...")
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        print("✅ Tabela encontrada!")
        
        # Clica no primeiro botão disponível
        xpath_botao = "//tbody/tr[1]//*[self::a or self::button]"
        botao = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_botao)))
        
        # Tenta pegar link direto
        link_final = botao.get_attribute('href')
        
        if not link_final or "http" not in link_final:
            print("🖱️ Clicando para descobrir link...")
            driver.execute_script("arguments[0].click();", botao)
            time.sleep(5)
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
            link_final = driver.current_url
            
        print(f"🔗 Link Final: {link_final}")
        
        # Download
        resp = requests.get(link_final, stream=True)
        if resp.status_code == 200:
            with open(caminho_pdf, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("💾 PDF Salvo.")
            return caminho_pdf, link_final
            
    except Exception as e:
        print(f"❌ ERRO FATAL: {e}")
        # DEBUG: Se falhar, mostra o código da página para sabermos o motivo
        if driver:
            try:
                print("--- DEBUG HTML (INÍCIO) ---")
                print(driver.page_source[:1000]) # Imprime os primeiros 1000 caracteres
                print("--- DEBUG HTML (FIM) ---")
            except:
                pass
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
    Analise o Diário Oficial de Teresópolis.
    Busque: Licitações, Pregões, Chamamentos, Obras.
    Ignore: Atos de RH (Férias, Nomeações).
    
    Se achar, formato:
    🚨 **[Nicho]**
    📦 **Objeto:** ...
    💰 **Valor:** ...
    
    Se nada: "ND"
    """
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": texto}
            ],
            temperature=0.3
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"Erro IA: {e}")
        return "ND"

# --- 5. TELEGRAM ---
def enviar_telegram(msg, link):
    print("📲 Enviando...")
    if not msg or "ND" in msg or "Nenhuma" in msg:
        texto = f"📊 *Monitor Teresópolis*\n✅ Monitoramento realizado.\nℹ️ Nenhuma oportunidade comercial hoje.\n🔗 [Link]({link})"
    else:
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
        print("❌ FALHA GERAL.")

if __name__ == "__main__":
    main()
