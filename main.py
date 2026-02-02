import os
import time
import glob
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

# --- DEFINIR PASTA DE DOWNLOAD ---
# No Linux (GitHub Actions), usamos /tmp. No Windows, pasta atual.
if os.name == 'nt':
    DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
else:
    DOWNLOAD_DIR = "/tmp"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# --- 1. CONFIGURAÇÃO DO DRIVER (COM AUTO-DOWNLOAD) ---
def configurar_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # PREFERÊNCIAS DE DOWNLOAD (O Segredo)
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True, # Força baixar PDF em vez de abrir no visualizador
        "profile.default_content_settings.popups": 0,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
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

# --- 2. SCRAPER ---
def buscar_e_baixar_diario():
    url_portal = "https://atos.teresopolis.rj.gov.br/diario/"
    driver = None
    
    # Limpa a pasta de downloads antes de começar
    for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*.pdf")):
        try: os.remove(f)
        except: pass

    print(f"🕵️  Acessando: {url_portal}")
    
    try:
        driver = configurar_driver()
        driver.set_page_load_timeout(90)
        driver.get(url_portal)
        
        # 1. Espera carregar a lista
        wait = WebDriverWait(driver, 30)
        print("⏳ Aguardando lista...")
        xpath_linha = "//*[contains(text(), 'Edição') and contains(text(), 'Ano')]"
        wait.until(EC.presence_of_all_elements_located((By.XPATH, xpath_linha)))
        
        # 2. Encontra a edição recente
        elementos = driver.find_elements(By.XPATH, xpath_linha)
        alvo = None
        for elem in elementos:
            # Pega o primeiro que tiver "202" (ano atual/recente)
            if elem.text and "202" in elem.text:
                alvo = elem
                break
        
        if alvo:
            print(f"🎯 Clicando em: '{alvo.text}'")
            driver.execute_script("arguments[0].click();", alvo)
            
            # 3. Espera a mudança de URL para pegar o ID
            print("⏳ Aguardando carregamento do visualizador...")
            time.sleep(10)
            
            url_atual = driver.current_url
            print(f"🔗 URL Atual: {url_atual}")
            
            # Extrai o ID da URL (ex: .../diario/3236 -> 3236)
            id_diario = None
            if "/diario/" in url_atual:
                try:
                    parts = url_atual.split("/diario/")
                    if len(parts) > 1:
                        id_diario = parts[-1].split("/")[0] # Garante pegar só o número
                except:
                    pass
            
            if id_diario:
                print(f"🆔 ID Identificado: {id_diario}")
                
                # 4. FORÇA O NAVEGADOR A BAIXAR (Usando a sessão logada)
                # Tenta URL de Download Direto
                link_download = f"https://atos.teresopolis.rj.gov.br/api/editions/download/{id_diario}"
                print(f"⬇️ Forçando navegador a ir para: {link_download}")
                driver.get(link_download)
                
                # Espera o arquivo aparecer na pasta
                caminho_final = aguardar_download(DOWNLOAD_DIR)
                
                # Se falhar, tenta URL alternativa (ViewPDF)
                if not caminho_final:
                    print("⚠️ Primeira tentativa falhou. Tentando rota alternativa 'viewPdf'...")
                    link_view = f"https://atos.teresopolis.rj.gov.br/api/editions/viewPdf/{id_diario}"
                    driver.get(link_view)
                    caminho_final = aguardar_download(DOWNLOAD_DIR)
                
                if caminho_final:
                    print(f"💾 PDF Baixado com sucesso: {caminho_final}")
                    return caminho_final, url_atual
                else:
                    print("❌ O navegador não salvou nenhum arquivo.")
            else:
                print("❌ Não consegui extrair o ID da edição da URL.")
        else:
            print("❌ Nenhuma edição recente encontrada na lista.")
            
        return None, None

    except Exception as e:
        print(f"❌ ERRO GERAL: {e}")
        return None, None
    finally:
        if driver:
            driver.quit()

# --- FUNÇÃO AUXILIAR: ESPERAR DOWNLOAD ---
def aguardar_download(pasta, timeout=30):
    print("⏳ Verificando pasta de downloads...")
    fim = time.time() + timeout
    while time.time() < fim:
        arquivos = glob.glob(os.path.join(pasta, "*.pdf"))
        if arquivos:
            # Pega o arquivo mais recente
            arquivo_mais_recente = max(arquivos, key=os.path.getctime)
            # Verifica se terminou de baixar (não tem .crdownload)
            if not arquivo_mais_recente.endswith(".crdownload"):
                # Verifica tamanho (> 1KB)
                if os.path.getsize(arquivo_mais_recente) > 1000:
                    return arquivo_mais_recente
        time.sleep(1)
    return None

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
            print("✅ SUCESSO TOTAL.")
        else:
            print("⚠️ PDF sem texto legível.")
            # enviar_telegram("ND", link) # Opcional: avisar erro
    else:
        print("❌ FALHA NO DOWNLOAD.")

if __name__ == "__main__":
    main()
