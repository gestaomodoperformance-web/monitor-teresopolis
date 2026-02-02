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

# --- 1. DRIVER BLINDADO ---
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

# --- 2. SCRAPER COM INJEÇÃO JS (IONIC) ---
def buscar_e_baixar_diario():
    url = "https://atos.teresopolis.rj.gov.br/diario/"
    caminho_pdf = "/tmp/diario_hoje.pdf" if os.name != 'nt' else "diario_hoje.pdf"
    driver = None
    
    print(f"🕵️  Acessando: {url}")
    
    try:
        driver = configurar_driver()
        driver.set_page_load_timeout(90)
        driver.get(url)
        
        print(f"📡 Título: {driver.title}")
        print("⏳ Aguardando 20 segundos para o Ionic montar a tela...")
        time.sleep(20) # Sites Ionic demoram para "hidratar" (montar) os botões
        
        print("💉 Injetando JavaScript para caçar botões...")
        
        # SCRIPT MÁGICO: Procura qualquer botão que tenha ícone de download/visualizar ou link de PDF
        # Essa função roda DENTRO do navegador da prefeitura
        link_encontrado = driver.execute_script("""
            // Busca todos os botões e links do Ionic
            var candidates = document.querySelectorAll('ion-button, a, button, ion-item');
            
            for (var i = 0; i < candidates.length; i++) {
                var el = candidates[i];
                var html = el.outerHTML.toLowerCase();
                var text = el.innerText.toLowerCase();
                
                // CRITÉRIOS DE DISPARO
                // Procura por ícones comuns de PDF ou palavras chaves
                if (html.includes('download') || 
                    html.includes('pdf') || 
                    html.includes('visualizar') || 
                    html.includes('print') || 
                    text.includes('visualizar') ||
                    text.includes('abrir')) {
                    
                    // Se achou, clica e avisa o Python
                    el.click();
                    return "CLICADO";
                }
            }
            return "NAO_ACHEI";
        """)
        
        if link_encontrado == "CLICADO":
            print("👆 JavaScript clicou em um botão candidato! Aguardando resposta...")
            time.sleep(10)
            
            # Verifica se abriu nova aba
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
                print("📑 Nova aba detectada.")
            
            link_final = driver.current_url
            print(f"🔗 URL Atual: {link_final}")
            
            # Tenta baixar o que estiver na URL (seja PDF ou blob)
            # Se for blob, usamos requests com headers do browser
            headers = {
                "User-Agent": driver.execute_script("return navigator.userAgent;")
            }
            
            resp = requests.get(link_final, headers=headers, stream=True, verify=False)
            
            # Salva sempre, depois tentamos ler
            with open(caminho_pdf, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            # Verificação básica se baixou algo > 1kb
            if os.path.getsize(caminho_pdf) > 1000:
                print("💾 Arquivo salvo com sucesso.")
                return caminho_pdf, link_final
            else:
                print("⚠️ Arquivo baixado está vazio.")
                return None, None
        else:
            print("❌ O JavaScript não encontrou nenhum botão óbvio de PDF.")
            # Última tentativa: Printar o BODY para ver onde o botão se escondeu
            # print(driver.find_element(By.TAG_NAME, "body").get_attribute('innerHTML')[:2000])
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
        print(f"⚠️ Erro leitura PDF: {e}")
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
        if len(texto) > 100: # Só analisa se tiver texto real
            resumo = analisar(texto)
            enviar_telegram(resumo, link)
            print("✅ FIM.")
        else:
            print("⚠️ PDF parece ser imagem ou está vazio.")
            # Envia aviso de erro no download/leitura
            # enviar_telegram("ND", link) 
    else:
        print("❌ FALHA NO DOWNLOAD.")

if __name__ == "__main__":
    main()
