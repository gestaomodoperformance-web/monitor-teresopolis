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
    
    print("🚗 Configurando Driver...")
    try:
        caminho = ChromeDriverManager().install()
        if "THIRD_PARTY_NOTICES" in caminho:
            pasta = os.path.dirname(caminho)
            caminho = os.path.join(pasta, "chromedriver")
        os.chmod(caminho, 0o755)
        service = Service(executable_path=caminho)
        return webdriver.Chrome(service=service, options=chrome_options)
    except Exception:
        return webdriver.Chrome(options=chrome_options)

# --- 2. SCRAPER TIPO "SCANNER" ---
def buscar_e_baixar_diario():
    url_sistema = "https://atos.teresopolis.rj.gov.br/diario/"
    caminho_pdf = "/tmp/diario_hoje.pdf" if os.name != 'nt' else "diario_hoje.pdf"
    driver = None
    
    print(f"🕵️  Acessando: {url_sistema}")
    
    try:
        driver = configurar_driver()
        driver.set_page_load_timeout(90)
        driver.get(url_sistema)
        
        print(f"📡 Título: {driver.title}")
        time.sleep(10) # Espera técnica para o Ionic "montar" a tela

        # Tenta achar qualquer coisa que pareça um item de lista
        print("🔍 Escaneando a página por links de PDF...")
        
        # Pega TODOS os elementos 'a' (links) e 'button' (botões)
        elementos = driver.find_elements(By.TAG_NAME, "a") + driver.find_elements(By.TAG_NAME, "button")
        
        link_candidato = None
        
        for elem in elementos:
            try:
                # Pega atributos para análise
                href = elem.get_attribute("href") or ""
                texto = elem.text.lower()
                classe = elem.get_attribute("class") or ""
                onclick = elem.get_attribute("onclick") or ""
                
                # CRITÉRIOS DE BUSCA (O que define o botão certo?)
                eh_pdf = ".pdf" in href
                tem_download = "download" in href or "download" in classe or "download" in texto
                eh_visualizar = "visualizar" in texto or "abrir" in texto
                tem_icone = "fa-file-pdf" in classe or "ion-icon" in elem.get_attribute("innerHTML")
                
                # Se for um link http válido e tiver cara de PDF/Download
                if href and "http" in href and (eh_pdf or tem_download or eh_visualizar):
                    print(f"🎯 Candidato encontrado: {href}")
                    link_candidato = href
                    break # Pega o primeiro que achar (geralmente é o mais recente no topo)
            except:
                continue

        # SE A BUSCA FALHAR, TENTA CLICAR NO PRIMEIRO ÍCONE VISÍVEL
        if not link_candidato:
            print("⚠️ Nenhum link óbvio. Tentando clicar no primeiro ícone da grade...")
            # Busca genérica por ícones comuns no sistema Mentor/Atos
            try:
                # Tenta clicar no primeiro elemento clicável dentro da área de conteúdo
                clicavel = driver.find_element(By.CSS_SELECTOR, "ion-row ion-col button, ion-row ion-col a, .fa-file-pdf")
                driver.execute_script("arguments[0].click();", clicavel)
                time.sleep(5)
                if len(driver.window_handles) > 1:
                    driver.switch_to.window(driver.window_handles[-1])
                link_candidato = driver.current_url
            except Exception as e:
                print(f"❌ Falha no clique de emergência: {e}")

        if link_candidato:
            print(f"🔗 Link Final: {link_candidato}")
            resp = requests.get(link_candidato, stream=True)
            if resp.status_code == 200:
                with open(caminho_pdf, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                print("💾 PDF Salvo.")
                return caminho_pdf, link_candidato
        
        print("❌ Nenhum PDF encontrado no scanner.")
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
    except:
        return ""

# --- 4. IA ---
def analisar(texto):
    print("🧠 Analisando...")
    prompt = """
    Analise o texto do Diário Oficial.
    Busque: Licitações, Pregões, Chamamentos, Obras.
    Ignore: Atos de RH.
    Se encontrar, liste: 🚨 [Nicho] | 📦 Objeto | 💰 Valor.
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
    if msg and "ND" not in msg and "Nenhuma" not in msg:
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
        print("❌ FALHA NO DOWNLOAD.")

if __name__ == "__main__":
    main()
