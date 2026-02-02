import os
import time
import glob
import json
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
# Tenta carregar .env apenas se existir (para testes locais)
try:
    from dotenv import load_dotenv
    load_dotenv("Chaves.env")
except:
    pass

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Validação de Segurança
if not OPENAI_API_KEY:
    print("❌ ERRO: Chaves de API não configuradas nos Secrets!")
    exit(1)

client = OpenAI(api_key=OPENAI_API_KEY)
urllib3.disable_warnings()

# --- 1. CONFIGURAÇÃO DO DRIVER (Modo Nuvem) ---
def configurar_driver():
    chrome_options = Options()
    
    # --- OBRIGATÓRIO PARA GITHUB ACTIONS ---
    chrome_options.add_argument("--headless=new") # Roda sem interface
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--ignore-certificate-errors")
    
    # Configura pasta de download fixa no ambiente Linux
    pasta_download = os.getcwd()
    prefs = {
        "download.default_directory": pasta_download,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True, # Força baixar PDF
        "profile.default_content_settings.popups": 0
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    print(f"📂 Pasta de Download configurada: {pasta_download}")
    
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)

# --- 2. CAÇADOR DE ARQUIVOS ---
def esperar_download(pasta, timeout=90):
    print("👀 Vigiando pasta por novos arquivos...")
    fim = time.time() + timeout
    
    while time.time() < fim:
        # Procura qualquer PDF na pasta
        arquivos = glob.glob(os.path.join(pasta, "*.pdf"))
        
        # Filtra para pegar apenas o que foi modificado agora
        if arquivos:
            recente = max(arquivos, key=os.path.getmtime)
            # Se o arquivo tiver mais de 10 segundos de idade, é velho, ignora
            if time.time() - os.path.getmtime(recente) < 30:
                # Verifica se terminou de baixar (.crdownload some)
                if ".crdownload" not in recente:
                    print(f"✅ Arquivo capturado: {os.path.basename(recente)}")
                    return recente
        
        time.sleep(1)
    return None

# --- 3. ROBÔ DE DOWNLOAD ---
def buscar_diario():
    url_portal = "https://atos.teresopolis.rj.gov.br/diario/"
    driver = None
    
    try:
        driver = configurar_driver()
        driver.set_page_load_timeout(120) # Tempo maior para servidor lento
        
        print(f"🕵️  Acessando portal...")
        driver.get(url_portal)
        
        wait = WebDriverWait(driver, 30)
        
        # Espera lista carregar
        print("⏳ Aguardando lista de edições...")
        xpath_linha = "//*[contains(text(), 'Edição') and contains(text(), 'Ano')]"
        wait.until(EC.presence_of_element_located((By.XPATH, xpath_linha)))
        elementos = driver.find_elements(By.XPATH, xpath_linha)
        
        # Filtro de Ano (2026)
        alvo = None
        for elem in elementos:
            if "2026" in elem.text:
                alvo = elem
                break
        if not alvo and elementos: alvo = elementos[0]

        if alvo:
            texto_alvo = alvo.text
            print(f"🎯 Alvo encontrado: '{texto_alvo}'")
            
            # Clica para abrir visualizador
            driver.execute_script("arguments[0].click();", alvo)
            print("⏳ Aguardando visualizador (15s)...")
            time.sleep(15) 
            
            # Tenta clicar no botão de download (Estratégia Vencedora)
            print("👇 Tentando clicar no botão de Download...")
            
            # Tenta via JS primeiro (mais garantido em headless)
            clicou = driver.execute_script("""
                var btn = document.querySelector('a[download]') || 
                          document.querySelector('button[title="Download"]') ||
                          document.querySelector('#download');
                if(btn) {
                    btn.click();
                    return true;
                }
                return false;
            """)
            
            if not clicou:
                print("⚠️ Botão JS falhou. Tentando via Selenium...")
                try:
                    btns = driver.find_elements(By.CSS_SELECTOR, "a[download], button[title='Download']")
                    if btns: btns[0].click()
                except: pass
            
            # Espera o arquivo cair na pasta
            arquivo_baixado = esperar_download(os.getcwd())
            
            if arquivo_baixado:
                # Renomeia para padronizar
                novo_nome = os.path.join(os.getcwd(), "diario_hoje.pdf")
                if os.path.exists(novo_nome): os.remove(novo_nome)
                os.rename(arquivo_baixado, novo_nome)
                return novo_nome, driver.current_url
            else:
                print("❌ Timeout: O arquivo não apareceu na pasta.")
        else:
            print("❌ Nenhuma edição encontrada na lista.")

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
            # Lê até 25 páginas
            for page in pdf.pages[:25]: 
                text += page.extract_text() or ""
        return text[:100000]
    except Exception as e:
        print(f"❌ Erro PDF: {e}")
        return ""

# --- 5. IA ---
def analisar(texto):
    print("🧠 Analisando com GPT...")
    prompt = """
    Analise o texto do Diário Oficial de Teresópolis-RJ.
    OBJETIVO: Identificar oportunidades de VENDAS para empresas (Licitações).
    
    BUSQUE:
    - Avisos de Licitação (Pregão, Tomada de Preços, Concorrência).
    - Chamamentos Públicos.
    - Dispensas de Licitação (compras diretas).
    
    IGNORE:
    - Nomeações, Exonerações, Férias, Licenças.
    - Decretos de Ponto Facultativo.
    
    SAÍDA (Markdown):
    🚨 **[TIPO]** Resumo curto do objeto
    💰 **Valor:** R$ X (se houver)
    📅 **Data:** Data da sessão (se houver)
    
    Se não houver NADA comercial, responda apenas: "ND"
    """
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": texto}],
            temperature=0.3
        )
        return resp.choices[0].message.content
    except: return "ND"

# --- 6. TELEGRAM ---
def enviar_telegram(msg, link):
    print("📲 Enviando Telegram...")
    data_hoje = time.strftime("%d/%m")
    
    if not msg or "ND" in msg or len(msg) < 10:
        texto = f"📊 *Monitor Teresópolis* ({data_hoje})\n✅ Diário verificado.\nℹ️ Nenhuma licitação nova encontrada.\n🔗 [Link Oficial]({link})"
    else:
        texto = f"📊 *Monitor Teresópolis* ({data_hoje})\n🚀 *Oportunidades Encontradas!*\n\n{msg}\n\n🔗 [Link Oficial]({link})"
        
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": texto, "parse_mode": "Markdown", "disable_web_page_preview": True
        })
        print("✅ Mensagem enviada!")
    except Exception as e:
        print(f"❌ Erro Telegram: {e}")

def main():
    print("--- INICIANDO BOT GITHUB ---")
    pdf, link = buscar_diario()
    
    if pdf:
        texto = extrair_texto(pdf)
        if len(texto) > 50:
            resumo = analisar(texto)
            enviar_telegram(resumo, link)
            print("🏁 SUCESSO.")
        else:
            print("⚠️ PDF sem texto (Imagem?).")
            enviar_telegram("⚠️ O Diário de hoje parece ser uma imagem digitalizada. Não consegui ler o texto.", link)
    else:
        print("❌ Falha no processo de download.")
        # Opcional: Avisar erro no Telegram
        # enviar_telegram("❌ Erro técnico ao tentar baixar o diário hoje.", "https://atos.teresopolis.rj.gov.br/diario/")

if __name__ == "__main__":
    main()
