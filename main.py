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

# --- 1. CONFIGURAÇÃO DO DRIVER (Blindada contra Crash) ---
def configurar_driver():
    chrome_options = Options()
    # A MUDANÇA CRUCIAL: Usar o novo modo headless
    chrome_options.add_argument("--headless=new") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--remote-debugging-port=9222") # Evita erros de porta
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    print("🚗 Configurando Driver...")
    try:
        caminho_driver = ChromeDriverManager().install()
        
        # Correção do Bug de Caminho (Linux)
        if "THIRD_PARTY_NOTICES" in caminho_driver:
            print("⚠️ Caminho corrigido (Bug Linux).")
            pasta_driver = os.path.dirname(caminho_driver)
            caminho_driver = os.path.join(pasta_driver, "chromedriver")
        
        # Dá permissão de execução
        try:
            os.chmod(caminho_driver, 0o755)
        except:
            pass
            
        service = Service(executable_path=caminho_driver)
        
    except Exception as e:
        print(f"⚠️ Erro no gerenciador: {e}")
        service = Service() # Tenta driver padrão do sistema

    return webdriver.Chrome(service=service, options=chrome_options)

# --- 2. ROBÔ DE DOWNLOAD (Scraper) ---
def buscar_e_baixar_diario():
    url_sistema = "https://atos.teresopolis.rj.gov.br/diario/"
    
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
        wait = WebDriverWait(driver, 40) # Aumentei o tempo de espera
        
        print("⏳ Aguardando carregamento da tabela...")
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        print("✅ Tabela encontrada.")
        
        # Tenta clicar no primeiro item da tabela (estratégia genérica)
        # Procura por qualquer tag 'a' ou 'button' na primeira linha
        print("👆 Buscando botão de download...")
        xpath_botao = "//tbody/tr[1]//*[self::a or self::button]"
        botao = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_botao)))
        
        driver.execute_script("arguments[0].scrollIntoView();", botao)
        time.sleep(2)
        
        # Tenta capturar o link ANTES de clicar (se possível)
        href = botao.get_attribute('href')
        if href and "http" in href:
            link_final = href
            print(f"🔗 Link extraído diretamente: {link_final}")
        else:
            # Se não tem link direto, clica
            print("🖱️ Clicando para abrir...")
            botao.click()
            time.sleep(8) # Espera maior para redirecionamento
            
            if len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
            
            link_final = driver.current_url
            print(f"🔗 Link capturado pós-clique: {link_final}")
        
        # Download
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(link_final, headers=headers, stream=True)
        
        if response.status_code == 200 and 'pdf' in response.headers.get('Content-Type', '').lower():
            with open(caminho_pdf, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("💾 PDF salvo com sucesso.")
            return caminho_pdf, link_final
        elif response.status_code == 200:
            # Às vezes o header não diz que é PDF, mas é. Tentamos salvar.
            with open(caminho_pdf, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("💾 Arquivo salvo (Check manual necessário).")
            return caminho_pdf, link_final
        else:
            print(f"❌ Erro HTTP ao baixar: {response.status_code}")
            return None, None
            
    except Exception as e:
        print(f"❌ Erro Fatal no Scraping: {e}")
        return None, None
    finally:
        if driver:
            driver.quit()

# --- 3. EXTRATOR ---
def extrair_texto_relevante(caminho_pdf):
    print("📖 Lendo PDF...")
    texto_bruto = ""
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for page in pdf.pages:
                texto_bruto += page.extract_text() + "\n"
        return texto_bruto[:100000] 
    except Exception as e:
        print(f"❌ Erro ao ler PDF (Pode não ser um PDF válido): {e}")
        return ""

# --- 4. IA ---
def analisar_oportunidades(texto_diario):
    print("🧠 Analisando...")
    prompt_sistema = """
    Você é um Analista de Licitações. Analise o texto.
    REGRAS:
    1. Ignore atos administrativos internos.
    2. Busque: Licitação, Pregão, Chamamento, Dispensa, Contratos.
    
    SAÍDA SE TIVER OPORTUNIDADE:
    🚨 **[Nicho]**
    📦 **Objeto:** Resumo.
    💰 **Valor:** R$ X
    
    SAÍDA SE NÃO TIVER NADA:
    "ND"
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": f"Texto:\n{texto_diario}"}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ Erro OpenAI: {e}")
        return "Erro IA"

# --- 5. TELEGRAM ---
def enviar_telegram(mensagem, link_original):
    print("📲 Enviando Telegram...")
    if not mensagem or mensagem.strip() == "ND" or "Nenhuma oportunidade" in mensagem:
        msg_final = (
            f"📊 *Monitor Estratégico Teresópolis* \n"
            f"📅 *Relatório Diário*\n\n"
            f"✅ *Status:* Monitoramento realizado.\n"
            f"ℹ️ *Resultado:* Nenhuma nova licitação identificada hoje.\n\n"
            f"🔗 [Acessar Documento]({link_original})"
        )
    else:
        cabecalho = f"📊 *Monitor Estratégico Teresópolis* \n🚀 *Oportunidades!* \n\n"
        rodape = f"\n🔗 [Baixar Edital]({link_original})"
        msg_final = cabecalho + mensagem + rodape
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg_final,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    requests.post(url, json=payload)

# --- MAIN ---
def main():
    print("--- INICIANDO ---")
    caminho_pdf, link_pdf = buscar_e_baixar_diario()
    
    if caminho_pdf and link_pdf:
        texto = extrair_texto_relevante(caminho_pdf)
        if texto:
            resumo = analisar_oportunidades(texto)
            enviar_telegram(resumo, link_pdf)
            print("✅ Ciclo concluído com sucesso.")
        else:
            print("⚠️ Texto vazio.")
    else:
        print("⚠️ Falha no download.")

if __name__ == "__main__":
    main()
