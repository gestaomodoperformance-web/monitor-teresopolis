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

# --- CONFIGURAÇÕES DE SEGURANÇA ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

# --- 1. CONFIGURAÇÃO DO DRIVER (COM CORREÇÃO DE BUG LINUX) ---
def configurar_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    print("🚗 Configurando Driver...")
    try:
        # Tenta baixar a versão mais recente
        caminho_driver = ChromeDriverManager().install()
        
        # --- CORREÇÃO DO BUG "THIRD_PARTY_NOTICES" ---
        # Se o gerenciador apontar para o arquivo de texto, forçamos o executável
        if "THIRD_PARTY_NOTICES" in caminho_driver:
            print("⚠️ Caminho incorreto detectado (Bug do Linux). Corrigindo...")
            pasta_driver = os.path.dirname(caminho_driver)
            caminho_driver = os.path.join(pasta_driver, "chromedriver")
        
        # Garante que o arquivo é executável (Permissão Linux)
        try:
            os.chmod(caminho_driver, 0o755)
        except:
            pass
            
        service = Service(executable_path=caminho_driver)
        
    except Exception as e:
        print(f"⚠️ Erro no gerenciador automático: {e}")
        print("🔄 Tentando usar driver do sistema...")
        # Fallback: Se tudo falhar, tenta usar o driver instalado pelo GitHub Actions (setup-chrome)
        service = Service()

    return webdriver.Chrome(service=service, options=chrome_options)

# --- 2. ROBÔ DE DOWNLOAD (Scraper) ---
def buscar_e_baixar_diario():
    url_sistema = "https://atos.teresopolis.rj.gov.br/diario/"
    
    # Define onde salvar
    if os.name == 'nt':
        caminho_pdf = "diario_hoje.pdf"
    else:
        caminho_pdf = "/tmp/diario_hoje.pdf"
        
    link_final = None
    driver = None
    
    print(f"🕵️  Acessando Portal: {url_sistema}")
    
    try:
        driver = configurar_driver()
        driver.get(url_sistema)
        wait = WebDriverWait(driver, 30)
        
        # Espera a tabela
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        print("✅ Tabela encontrada. Buscando edição mais recente...")
        
        xpath_botao = "//tbody/tr[1]//button | //tbody/tr[1]//a[contains(@class, 'btn') or .//i]"
        botao = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_botao)))
        
        driver.execute_script("arguments[0].scrollIntoView();", botao)
        time.sleep(1)
        botao.click()
        
        time.sleep(5)
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
        
        link_final = driver.current_url
        print(f"🔗 Link capturado: {link_final}")
        
        response = requests.get(link_final, stream=True)
        if response.status_code == 200:
            with open(caminho_pdf, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("💾 PDF baixado com sucesso.")
            return caminho_pdf, link_final
        else:
            print("❌ Erro ao baixar o arquivo físico.")
            return None, None
            
    except Exception as e:
        print(f"❌ Erro durante o scraping: {e}")
        return None, None
    finally:
        if driver:
            driver.quit()

# --- 3. EXTRATOR DE TEXTO ---
def extrair_texto_relevante(caminho_pdf):
    print("📖 Lendo conteúdo do PDF...")
    texto_bruto = ""
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for page in pdf.pages:
                texto_bruto += page.extract_text() + "\n"
        return texto_bruto[:100000] 
    except Exception as e:
        print(f"❌ Erro ao ler PDF: {e}")
        return ""

# --- 4. INTELIGÊNCIA ARTIFICIAL (Analista) ---
def analisar_oportunidades(texto_diario):
    print("🧠 Enviando para análise da IA...")
    
    prompt_sistema = """
    Você é um Analista de Licitações Públicas. Sua missão é ler o Diário Oficial e encontrar dinheiro na mesa.
    
    REGRAS:
    1. Ignore: Nomeações, Férias, Exonerações, Decretos administrativos, Leis sem impacto comercial.
    2. Busque: Aviso de Licitação, Pregão, Chamamento Público, Dispensa de Licitação, Contratos Assinados.
    
    SAÍDA ESPERADA (Se encontrar algo):
    Para cada item, gere este bloco:
    🚨 **[Nicho]** (Ex: Obras, Eventos, TI, Saúde)
    📦 **Objeto:** Resumo curto do que é.
    💰 **Valor:** R$ X (se tiver)
    📅 **Data:** Data da sessão (se tiver)
    
    SAÍDA ESPERADA (Se NÃO encontrar nada comercial):
    Responda EXATAMENTE apenas a palavra: "ND"
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": f"Texto extraído do Diário Oficial:\n{texto_diario}"}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ Erro na API OpenAI: {e}")
        return "Erro na análise."

# --- 5. ENVIAR PARA TELEGRAM (Com Heartbeat) ---
def enviar_telegram(mensagem, link_original):
    print("📲 Preparando envio para o Telegram...")
    
    if not mensagem or mensagem.strip() == "ND" or "Nenhuma oportunidade" in mensagem:
        msg_final = (
            f"📊 *Monitor Estratégico Teresópolis* \n"
            f"📅 *Relatório Diário*\n\n"
            f"✅ *Status:* Monitoramento realizado.\n"
            f"ℹ️ *Resultado:* Nenhuma nova licitação ou oportunidade comercial identificada na edição de hoje.\n\n"
            f"🔗 [Acessar Documento Oficial]({link_original})"
        )
    else:
        cabecalho = f"📊 *Monitor Estratégico Teresópolis* \n🚀 *Novas Oportunidades Detectadas!* \n\n"
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
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("🚀 Mensagem enviada com sucesso!")
        else:
            print(f"❌ Falha no envio Telegram: {response.text}")
    except Exception as e:
        print(f"❌ Erro de conexão Telegram: {e}")

# --- ORQUESTRADOR PRINCIPAL ---
def main():
    print("--- INICIANDO MONITORAMENTO ---")
    caminho_pdf, link_pdf = buscar_e_baixar_diario()
    
    if caminho_pdf and link_pdf:
        texto = extrair_texto_relevante(caminho_pdf)
        if texto:
            resumo_ia = analisar_oportunidades(texto)
            enviar_telegram(resumo_ia, link_pdf)
        else:
            print("⚠️ PDF estava vazio ou ilegível.")
    else:
        print("⚠️ Não foi possível baixar o Diário hoje.")

if __name__ == "__main__":
    main()
