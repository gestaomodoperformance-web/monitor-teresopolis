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

# --- 1. DRIVER ---
def configurar_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
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

# --- 2. SCRAPER (COOKIE STEALING) ---
def buscar_e_baixar_diario():
    url_portal = "https://atos.teresopolis.rj.gov.br/diario/"
    caminho_pdf = "diario_hoje.pdf" if os.name == 'nt' else "/tmp/diario_hoje.pdf"
    driver = None
    
    print(f"🕵️  Acessando: {url_portal}")
    
    try:
        driver = configurar_driver()
        driver.set_page_load_timeout(90)
        driver.get(url_portal)
        
        wait = WebDriverWait(driver, 30)
        print("⏳ Aguardando lista...")
        xpath_linha = "//*[contains(text(), 'Edição') and contains(text(), 'Ano')]"
        wait.until(EC.presence_of_all_elements_located((By.XPATH, xpath_linha)))
        
        # --- CORREÇÃO DA LÓGICA DE DATA ---
        elementos = driver.find_elements(By.XPATH, xpath_linha)
        melhor_candidato = None
        
        print(f"📋 Analisando {len(elementos)} edições...")
        
        # Pega o PRIMEIRO item que contém "2026" (A lista já vem ordenada por data decrescente)
        for elem in elementos:
            texto = elem.text
            if "2026" in texto:
                melhor_candidato = elem
                break # Achou o mais recente de 2026, para.
        
        # Se não achar 2026, tenta o primeiro da lista (fallback)
        if not melhor_candidato and elementos:
            melhor_candidato = elementos[0]
            print("⚠️ Nenhuma de 2026 achada. Pegando a mais recente disponível.")
        
        if melhor_candidato:
            print(f"🎯 Alvo Selecionado: '{melhor_candidato.text}'")
            
            # Clica para gerar o ID na URL
            driver.execute_script("arguments[0].click();", melhor_candidato)
            time.sleep(8)
            
            url_atual = driver.current_url
            id_diario = None
            
            if "/diario/" in url_atual:
                try:
                    id_diario = url_atual.split("/")[-1]
                except:
                    id_diario = None
            
            if id_diario and id_diario.isdigit():
                link_api = f"https://atos.teresopolis.rj.gov.br/api/editions/download/{id_diario}"
                print(f"⚡ URL da API: {link_api}")
                
                # --- TÉCNICA: ROUBO DE COOKIES ---
                print("🍪 Roubando cookies da sessão do Selenium...")
                selenium_cookies = driver.get_cookies()
                
                # Prepara a sessão do Requests
                session = requests.Session()
                for cookie in selenium_cookies:
                    session.cookies.set(cookie['name'], cookie['value'])
                
                # Headers de navegador real
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://atos.teresopolis.rj.gov.br/diario/",
                    "Accept": "application/pdf,application/octet-stream"
                }
                
                print("⬇️ Baixando arquivo via Python (Requests)...")
                response = session.get(link_api, headers=headers, stream=True, verify=False)
                
                if response.status_code == 200:
                    with open(caminho_pdf, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                            
                    tamanho = os.path.getsize(caminho_pdf)
                    print(f"📦 Tamanho do arquivo: {tamanho} bytes")
                    
                    if tamanho > 2000:
                        print("✅ PDF Baixado com Sucesso!")
                        return caminho_pdf, url_atual
                    else:
                        print("❌ Arquivo baixado é muito pequeno (Erro de permissão?).")
                else:
                    print(f"❌ Erro HTTP: {response.status_code}")
            else:
                print("❌ ID não encontrado na URL.")
        else:
            print("❌ Nenhuma edição encontrada.")
            
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
    except Exception as e:
        print(f"❌ Erro leitura PDF: {e}")
        return ""

# --- 4. IA ---
def analisar(texto):
    print("🧠 Analisando...")
    prompt = """
    Analise o texto do Diário Oficial.
    Busque: Licitações, Pregões, Chamamentos, Obras, Contratos.
    Ignore: Atos de RH, Férias, Nomeações.
    
    Se encontrar, liste:
    🚨 **[Nicho]**
    📦 **Objeto:** Resumo
    💰 **Valor:** R$ X
    
    Se nada comercial: "ND"
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
    print("📲 Enviando Telegram...")
    texto = f"📊 *Monitor Teresópolis*\nℹ️ Nenhuma oportunidade comercial hoje.\n🔗 [Link]({link})"
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
        if len(texto) > 100:
            resumo = analisar(texto)
            enviar_telegram(resumo, link)
            print("✅ CICLO FINALIZADO.")
        else:
            print("⚠️ PDF sem texto legível.")
            # enviar_telegram("ND", link)
    else:
        print("❌ FALHA NO DOWNLOAD.")

if __name__ == "__main__":
    main()
