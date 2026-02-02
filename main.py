import os
import time
import requests
import pdfplumber
import base64
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

# --- 1. CONFIGURAÇÃO DO DRIVER ---
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

# --- 2. SCRAPER ---
def buscar_e_baixar_diario():
    url = "https://atos.teresopolis.rj.gov.br/diario/"
    caminho_pdf = "/tmp/diario_hoje.pdf" if os.name != 'nt' else "diario_hoje.pdf"
    driver = None
    
    print(f"🕵️  Acessando: {url}")
    
    try:
        driver = configurar_driver()
        driver.set_page_load_timeout(90)
        driver.get(url)
        
        # Espera a lista carregar procurando pela palavra "Ano" ou "Edição"
        wait = WebDriverWait(driver, 30)
        print("⏳ Aguardando carregamento da lista...")
        
        # Procura elementos que contenham "Edição" E "Ano" (Baseado no seu Log)
        xpath_linha = "//*[contains(text(), 'Edição') and contains(text(), 'Ano')]"
        wait.until(EC.presence_of_all_elements_located((By.XPATH, xpath_linha)))
        
        # Pega todos os candidatos
        elementos = driver.find_elements(By.XPATH, xpath_linha)
        
        alvo_real = None
        for elem in elementos:
            # Filtra elementos vazios ou invisíveis
            if elem.text and len(elem.text) > 5 and "202" in elem.text: # Busca algo com ano 202x
                alvo_real = elem
                break
        
        if alvo_real:
            print(f"🎯 Clique Confirmado em: '{alvo_real.text}'")
            
            # Clica via JavaScript para garantir
            driver.execute_script("arguments[0].click();", alvo_real)
            
            print("👆 Clicado. Aguardando abertura do documento (15s)...")
            time.sleep(15)
            
            link_final = None
            
            # --- ESTRATÉGIA 1: Nova Aba ---
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
                link_final = driver.current_url
                print("📑 Nova aba detectada.")

            # --- ESTRATÉGIA 2: Visualizador Embutido (Embed/Iframe) ---
            else:
                print("🔍 Procurando PDF embutido na página...")
                try:
                    # Procura tags <embed>, <iframe> ou <object> que tenham pdf no src
                    pdf_embed = driver.execute_script("""
                        var tags = document.querySelectorAll('embed, iframe, object');
                        for(var i=0; i<tags.length; i++){
                            if(tags[i].src && tags[i].src.includes('blob')){
                                return tags[i].src;
                            }
                            if(tags[i].src && tags[i].src.includes('.pdf')){
                                return tags[i].src;
                            }
                        }
                        return null;
                    """)
                    
                    if pdf_embed:
                        link_final = pdf_embed
                        print(f"🔗 PDF Embutido encontrado: {link_final}")
                    else:
                        link_final = driver.current_url # Tenta a URL atual como fallback
                except:
                    pass

            # --- DOWNLOAD ---
            if link_final:
                print(f"⬇️ Baixando de: {link_final}")
                
                # Se for BLOB (Blob:https://...), precisa de JS especial
                if "blob:" in link_final:
                    js_blob = """
                        var uri = arguments[0];
                        var callback = arguments[1];
                        var xhr = new XMLHttpRequest();
                        xhr.responseType = 'blob';
                        xhr.onload = function() {
                            var reader = new FileReader();
                            reader.onloadend = function() { callback(reader.result); }
                            reader.readAsDataURL(xhr.response);
                        };
                        xhr.open('GET', uri);
                        xhr.send();
                    """
                    base64_data = driver.execute_async_script(js_blob, link_final)
                    data = base64.b64decode(base64_data.split(',')[1])
                    with open(caminho_pdf, 'wb') as f:
                        f.write(data)
                else:
                    # Download HTTP normal
                    resp = requests.get(link_final, stream=True, verify=False)
                    with open(caminho_pdf, 'wb') as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)

                # Verifica se baixou algo útil (>1KB)
                if os.path.exists(caminho_pdf) and os.path.getsize(caminho_pdf) > 1000:
                    print("💾 PDF Salvo com Sucesso.")
                    return caminho_pdf, link_final
                else:
                    print("⚠️ Arquivo baixado vazio ou inválido.")
            else:
                print("❌ Não foi possível extrair a URL do PDF após o clique.")

        else:
            print("❌ Nenhum texto 'Edição/Ano' visível encontrado.")

        return None, None

    except Exception as e:
        print(f"❌ ERRO TÉCNICO: {e}")
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
    texto_base = f"📊 *Monitor Teresópolis*\nℹ️ Nenhuma oportunidade comercial hoje.\n🔗 [Link Original]({link})"
    
    if msg and "ND" not in msg and "Nenhuma" not in msg:
        texto_base = f"📊 *Monitor Teresópolis*\n🚀 *Oportunidades Encontradas!*\n\n{msg}\n\n🔗 [Baixar Edital]({link})"
        
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto_base,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    })

def main():
    pdf, link = buscar_e_baixar_diario()
    if pdf and link:
        texto = extrair_texto(pdf)
        if len(texto) > 50:
            resumo = analisar(texto)
            enviar_telegram(resumo, link)
            print("✅ CICLO COMPLETO COM SUCESSO.")
        else:
            print("⚠️ PDF lido, mas sem texto (imagem?).")
    else:
        print("❌ FALHA NO PROCESSO.")

if __name__ == "__main__":
    main()
