import os
import time
import json
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

# --- 1. DRIVER COM ESCUTA DE REDE (A Mágica) ---
def configurar_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # ATIVANDO O LOG DE PERFORMANCE (Para interceptar o link do PDF)
    chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    
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

# --- 2. FUNÇÃO FAREJADORA ---
def encontrar_pdf_nos_logs(driver):
    print("👃 Farejando tráfego de rede em busca do PDF real...")
    logs = driver.get_log('performance')
    
    candidatos = []
    
    for entry in logs:
        try:
            message = json.loads(entry['message'])['message']
            
            # Procura requisições de resposta (Response)
            if message['method'] == 'Network.responseReceived':
                url = message['params']['response']['url']
                mime_type = message['params']['response']['mimeType']
                
                # O Pulo do Gato: Se o tipo for PDF ou a URL tiver cara de API de download
                if "application/pdf" in mime_type or ".pdf" in url or "download" in url:
                    # Ignora scripts .js ou css
                    if ".js" not in url and ".css" not in url and ".html" not in url:
                        candidatos.append(url)
        except:
            pass
            
    # Retorna o último encontrado (geralmente é o clique mais recente)
    if candidatos:
        return candidatos[-1]
    return None

# --- 3. SCRAPER PRINCIPAL ---
def buscar_e_baixar_diario():
    url_portal = "https://atos.teresopolis.rj.gov.br/diario/"
    caminho_pdf = "/tmp/diario_hoje.pdf" if os.name != 'nt' else "diario_hoje.pdf"
    driver = None
    
    print(f"🕵️  Acessando: {url_portal}")
    
    try:
        driver = configurar_driver()
        driver.set_page_load_timeout(90)
        driver.get(url_portal)
        
        # Espera lista carregar
        wait = WebDriverWait(driver, 30)
        print("⏳ Aguardando lista...")
        xpath_linha = "//*[contains(text(), 'Edição') and contains(text(), 'Ano')]"
        wait.until(EC.presence_of_all_elements_located((By.XPATH, xpath_linha)))
        
        # Encontra o elemento
        elementos = driver.find_elements(By.XPATH, xpath_linha)
        alvo = None
        for elem in elementos:
            if elem.text and "202" in elem.text:
                alvo = elem
                break
        
        if alvo:
            print(f"🎯 Clicando em: '{alvo.text}'")
            # Limpa os logs antigos antes de clicar
            driver.get_log('performance')
            
            # Clica
            driver.execute_script("arguments[0].click();", alvo)
            
            print("⏳ Aguardando requisição do arquivo (15s)...")
            time.sleep(15)
            
            # TENTA FAREJAR A URL REAL NO NETWORK
            url_real = encontrar_pdf_nos_logs(driver)
            
            # SE NÃO ACHOU NO LOG, TENTA MONTAR A URL DA API (Plano B)
            if not url_real:
                # O URL atual é .../diario/3240. O ID é 3240.
                url_atual = driver.current_url
                if "/diario/" in url_atual:
                    try:
                        id_diario = url_atual.split("/")[-1]
                        # Padrão comum da API da Atos/Mentor
                        url_real = f"https://atos.teresopolis.rj.gov.br/api/editions/download/{id_diario}"
                        print(f"⚠️ Log vazio. Tentando URL montada manualmente: {url_real}")
                    except:
                        pass
            
            if url_real:
                print(f"⬇️ Baixando URL Real: {url_real}")
                
                # Download com headers simulando o navegador
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://atos.teresopolis.rj.gov.br/"
                }
                
                resp = requests.get(url_real, headers=headers, stream=True, verify=False)
                
                with open(caminho_pdf, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                # Verifica se o arquivo é válido (tem cabeçalho de PDF ou tamanho decente)
                if os.path.exists(caminho_pdf) and os.path.getsize(caminho_pdf) > 2000:
                    # Verifica cabeçalho do arquivo
                    with open(caminho_pdf, 'rb') as f:
                        header = f.read(4)
                        if b'%PDF' in header:
                            print("✅ Arquivo validado: É um PDF real!")
                            return caminho_pdf, url_real
                        else:
                            print("⚠️ Arquivo baixado não começa com %PDF. Tentando ler mesmo assim...")
                            return caminho_pdf, url_real
                else:
                    print("❌ Arquivo baixado muito pequeno (provavelmente erro).")
            else:
                print("❌ Não foi possível interceptar a URL do PDF.")
        
        return None, None

    except Exception as e:
        print(f"❌ ERRO GERAL: {e}")
        return None, None
    finally:
        if driver:
            driver.quit()

# --- 4. EXTRATOR ---
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

# --- 5. IA ---
def analisar(texto):
    print("🧠 Analisando...")
    prompt = """
    Analise o texto do Diário Oficial.
    Busque: Licitações, Pregões, Chamamentos, Obras, Contratos.
    Ignore: Atos de RH (Nomeações, Exonerações).
    
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

# --- 6. TELEGRAM ---
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
            print("✅ SUCESSO TOTAL.")
        else:
            print("⚠️ PDF sem texto legível.")
    else:
        print("❌ FALHA NO DOWNLOAD.")

if __name__ == "__main__":
    main()
