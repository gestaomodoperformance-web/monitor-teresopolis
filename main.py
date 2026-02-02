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

# --- 1. DRIVER ---
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
        
        # Espera pelo texto chave que aparece nas linhas (ex: "Regular" ou "Ano")
        wait = WebDriverWait(driver, 30)
        print("⏳ Aguardando lista de edições...")
        
        # Estratégia Sniper: Espera aparecer qualquer elemento que contenha "Regular" ou "Extraordinário"
        # Isso garante que a lista carregou
        xpath_texto = "//*[contains(text(), 'Regular') or contains(text(), 'Extraordinário')]"
        wait.until(EC.presence_of_element_located((By.XPATH, xpath_texto)))
        
        print("✅ Lista carregada! Buscando a edição mais recente...")
        
        # Pega todos os elementos que têm esse texto (o primeiro costuma ser o mais recente no topo)
        elementos = driver.find_elements(By.XPATH, xpath_texto)
        
        if elementos:
            alvo = elementos[0] # Pega o primeiro da lista (topo)
            print(f"🎯 Alvo encontrado: '{alvo.text}'")
            
            # Clica no alvo (pode ser a linha inteira)
            driver.execute_script("arguments[0].click();", alvo)
            print("👆 Clique realizado. Aguardando reação...")
            time.sleep(10)
            
            # --- CENÁRIO A: Abriu nova aba (Link direto) ---
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
                link = driver.current_url
                print(f"🔗 Nova aba detectada: {link}")
                
                # Baixa
                resp = requests.get(link, stream=True, verify=False)
                with open(caminho_pdf, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                return caminho_pdf, link

            # --- CENÁRIO B: Abriu um visualizador na mesma tela (Blob/Embed) ---
            else:
                print("🔗 Mesma aba. Verificando se é Blob ou PDF embutido...")
                # Tenta extrair URL de algum embed/iframe
                url_atual = driver.current_url
                if "blob:" in url_atual or "pdf" in url_atual:
                     print(f"🔗 URL Detectada: {url_atual}")
                     # Se for blob, precisamos de JS para baixar
                     if "blob:" in url_atual:
                         print("⚡ Baixando BLOB via JavaScript...")
                         js_download = """
                            var uri = arguments[0];
                            var callback = arguments[1];
                            var xhr = new XMLHttpRequest();
                            xhr.responseType = 'blob';
                            xhr.onload = function() {
                                var reader = new FileReader();
                                reader.onloadend = function() {
                                    callback(reader.result);
                                }
                                reader.readAsDataURL(xhr.response);
                            };
                            xhr.open('GET', uri);
                            xhr.send();
                         """
                         uri = url_atual
                         result = driver.execute_async_script(js_download, uri)
                         # Salva o base64
                         base64_data = result.split(',')[1]
                         with open(caminho_pdf, 'wb') as f:
                             f.write(base64.b64decode(base64_data))
                         return caminho_pdf, url_atual
                     else:
                         # Download normal
                         resp = requests.get(url_atual, stream=True, verify=False)
                         with open(caminho_pdf, 'wb') as f:
                            for chunk in resp.iter_content(chunk_size=8192):
                                f.write(chunk)
                         return caminho_pdf, url_atual
                
                # CENÁRIO C: Clicou e abriu um Modal com botão "Baixar"
                print("🔎 Procurando botão 'Baixar' ou ícone dentro de modal...")
                try:
                    botao_modal = driver.find_element(By.XPATH, "//*[contains(text(), 'Baixar') or contains(text(), 'Download')]")
                    botao_modal.click()
                    time.sleep(10)
                    # Verifica abas de novo
                    if len(driver.window_handles) > 1:
                        driver.switch_to.window(driver.window_handles[-1])
                        link = driver.current_url
                        resp = requests.get(link, stream=True, verify=False)
                        with open(caminho_pdf, 'wb') as f:
                             for chunk in resp.iter_content(chunk_size=8192):
                                 f.write(chunk)
                        return caminho_pdf, link
                except:
                    pass

        print("❌ Não consegui baixar. Dumping texto da tela para análise:")
        print(driver.find_element(By.TAG_NAME, "body").text[:500])
        return None, None

    except Exception as e:
        print(f"❌ ERRO: {e}")
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
        # Verifica tamanho
        if os.path.exists(pdf) and os.path.getsize(pdf) > 1000:
            texto = extrair_texto(pdf)
            resumo = analisar(texto)
            enviar_telegram(resumo, link)
            print("✅ FIM.")
        else:
             print("⚠️ Arquivo vazio ou corrompido.")
    else:
        print("❌ FALHA NO DOWNLOAD.")

if __name__ == "__main__":
    main()
