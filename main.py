import os
import time
import base64
import requests
import pdfplumber
from datetime import datetime
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

# --- 2. SCRAPER (JS FETCH) ---
def buscar_e_baixar_diario():
    url_portal = "https://atos.teresopolis.rj.gov.br/diario/"
    # Define caminho compatível com Windows e Linux
    if os.name == 'nt':
        caminho_pdf = "diario_hoje.pdf"
    else:
        caminho_pdf = "/tmp/diario_hoje.pdf"
        
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
        
        # --- LÓGICA DE ORDENAÇÃO ---
        elementos = driver.find_elements(By.XPATH, xpath_linha)
        melhor_candidato = None
        maior_edicao = 0
        
        print(f"📋 Analisando {len(elementos)} edições encontradas...")
        
        for elem in elementos:
            texto = elem.text
            # Ex: "Edição 22 / Ano 11..."
            if "Edição" in texto and "202" in texto: 
                try:
                    num_edicao = int(texto.split("/")[0].replace("Edição", "").strip())
                    if num_edicao > maior_edicao:
                        maior_edicao = num_edicao
                        melhor_candidato = elem
                except:
                    continue
        
        if melhor_candidato:
            print(f"🎯 Alvo Selecionado (Mais recente): '{melhor_candidato.text}'")
            
            # Clica para entrar na página de detalhes
            driver.execute_script("arguments[0].click();", melhor_candidato)
            time.sleep(8)
            
            # Pega o ID da URL
            url_atual = driver.current_url
            id_diario = None
            
            # --- CORREÇÃO DO ERRO DE SINTAXE AQUI ---
            if "/diario/" in url_atual:
                try:
                    # Pega o último pedaço da URL (o número)
                    id_diario = url_atual.split("/")[-1]
                except:
                    id_diario = None
            
            if id_diario and id_diario.isdigit():
                link_api = f"https://atos.teresopolis.rj.gov.br/api/editions/download/{id_diario}"
                print(f"⚡ URL da API identificada: {link_api}")
                
                # --- O GRANDE TRUQUE: JS FETCH ---
                print("💉 Injetando JavaScript para download direto na memória RAM...")
                
                script_download = """
                    var url = arguments[0];
                    var callback = arguments[1];
                    
                    fetch(url)
                        .then(response => {
                            if (!response.ok) throw new Error('Network response was not ok');
                            return response.blob();
                        })
                        .then(blob => {
                            var reader = new FileReader();
                            reader.readAsDataURL(blob); 
                            reader.onloadend = function() {
                                callback(reader.result); // Retorna Base64 para o Python
                            }
                        })
                        .catch(error => {
                            callback("ERRO: " + error.message);
                        });
                """
                
                # Executa o script e espera o resultado
                resultado_base64 = driver.execute_async_script(script_download, link_api)
                
                if resultado_base64 and "base64," in str(resultado_base64):
                    # Decodifica e salva
                    print("💾 Recebido arquivo codificado. Salvando no disco...")
                    conteudo = base64.b64decode(resultado_base64.split(",")[1])
                    
                    with open(caminho_pdf, "wb") as f:
                        f.write(conteudo)
                        
                    if os.path.getsize(caminho_pdf) > 2000:
                        print("✅ PDF Salvo e Validado!")
                        return caminho_pdf, url_atual
                    else:
                        print("❌ Arquivo salvo mas é muito pequeno/vazio.")
                else:
                    print(f"❌ Falha no script JS: {resultado_base64}")
            else:
                print("❌ Não foi possível isolar o ID do diário na URL.")
        else:
            print("❌ Nenhuma edição válida encontrada.")
            
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
    else:
        print("❌ FALHA NO DOWNLOAD.")

if __name__ == "__main__":
    main()
