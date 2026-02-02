import os
import time
import requests
import pdfplumber
import json
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

# --- 1. CONFIGURAÇÃO DO DRIVER (SELENIUM) ---
def configurar_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)

# --- 2. SCRAPER DO DIÁRIO ---
def buscar_e_baixar_diario():
    url_sistema = "https://atos.teresopolis.rj.gov.br/diario/"
    driver = configurar_driver()
    caminho_pdf = "/tmp/diario_hoje.pdf"
    link_final = None
    
    print(f"🕵️  Acessando: {url_sistema}")
    
    try:
        driver.get(url_sistema)
        wait = WebDriverWait(driver, 30)
        
        # Espera tabela carregar
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        print("✅ Tabela encontrada. Buscando último edital...")
        
        # Busca ícones de PDF ou botões de download na primeira linha
        xpath_botao = "//tbody/tr[1]//button | //tbody/tr[1]//a[contains(@class, 'btn') or .//i]"
        botao = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_botao)))
        
        # Scroll e Click
        driver.execute_script("arguments[0].scrollIntoView();", botao)
        time.sleep(1)
        botao.click()
        time.sleep(5)
        
        # Gerencia abas (caso abra em nova janela)
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
        
        link_final = driver.current_url
        print(f"🔗 Link detectado: {link_final}")
        
        # Baixa o PDF
        response = requests.get(link_final, stream=True)
        if response.status_code == 200:
            # Em ambiente local Windows, ajustar /tmp para pasta local se for testar
            # Mas para GitHub Actions (Linux), /tmp é perfeito.
            if os.name == 'nt': # Se for Windows
                caminho_pdf = "diario_hoje.pdf"
            
            with open(caminho_pdf, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("💾 PDF baixado com sucesso.")
            return caminho_pdf, link_final
            
    except Exception as e:
        print(f"❌ Erro no Scraping: {e}")
        return None, None
    finally:
        driver.quit()
    
    return None, None

# --- 3. EXTRATOR DE TEXTO ---
def extrair_texto_relevante(caminho_pdf):
    print("📖 Lendo PDF...")
    texto_bruto = ""
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for page in pdf.pages:
                texto_bruto += page.extract_text() + "\n"
        return texto_bruto[:100000] 
    except Exception as e:
        print(f"❌ Erro ao ler PDF: {e}")
        return ""

# --- 4. ANALISTA IA (GPT-4o-mini) ---
def analisar_oportunidades(texto_diario):
    print("🧠 Analisando com IA...")
    
    prompt_sistema = """
    Você é um Analista de Licitações. Analise o texto do Diário Oficial.
    Ignore: Nomeações, Férias, Exonerações.
    Foque em: Licitações, Chamamentos, Compras, Avisos de Contratação.
    
    Para cada oportunidade, retorne NO MÁXIMO 3 linhas no formato:
    🚨 [Nicho]
    📦 Objeto: [Resumo]
    💰 Valor: [Se houver]
    
    Se não houver nada relevante, responda apenas: "ND"
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": f"Texto do Diário:\n{texto_diario}"}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ Erro na IA: {e}")
        return "Erro na análise de IA."

# --- 5. DISPARADOR TELEGRAM ---
def enviar_telegram(mensagem, link_original):
    if not mensagem or mensagem == "ND" or "Nenhuma oportunidade" in mensagem:
        print("🔕 Nada relevante hoje.")
        return

    cabecalho = f"📊 *Monitor Teresópolis* \n\n"
    rodape = f"\n🔗 [Baixar Edital Completo]({link_original})"
    msg_final = cabecalho + mensagem + rodape
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg_final,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload)
        print("🚀 Notificação enviada!")
    except Exception as e:
        print(f"Erro Telegram: {e}")

# --- ORQUESTRADOR ---
def main():
    caminho_pdf, link_pdf = buscar_e_baixar_diario()
    
    if caminho_pdf and link_pdf:
        texto = extrair_texto_relevante(caminho_pdf)
        if texto:
            resumo = analisar_oportunidades(texto)
            enviar_telegram(resumo, link_pdf)
    else:
        print("⚠️ Não foi possível obter o diário hoje.")

if __name__ == "__main__":
    main()