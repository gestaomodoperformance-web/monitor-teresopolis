import os
import time
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

# --- CONFIGURAÇÕES GERAIS ---
# Desabilita avisos de segurança SSL (limpa o log)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
    
    # Otimização de carregamento
    chrome_options.page_load_strategy = 'eager' # Não espera carregar imagens/css pesados
    
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

# --- 2. SCRAPER BLINDADO ---
def buscar_e_baixar_diario():
    url_portal = "https://atos.teresopolis.rj.gov.br/diario/"
    caminho_pdf = "diario_hoje.pdf" if os.name == 'nt' else "/tmp/diario_hoje.pdf"
    driver = None
    
    print("--- ETAPA 1: INICIANDO ACESSO ---")
    
    try:
        driver = configurar_driver()
        # Define limite de 60s para carregar a página (evita travamento infinito)
        driver.set_page_load_timeout(60)
        
        print(f"🕵️  Navegando para: {url_portal}")
        driver.get(url_portal)
        
        print("--- ETAPA 2: BUSCANDO EDIÇÃO ---")
        wait = WebDriverWait(driver, 30) # Espera máxima de 30s pela lista
        
        # Procura linhas que contenham "Edição" e "Regular" ou "Extraordinário"
        xpath_linha = "//*[contains(text(), 'Edição') and (contains(text(), 'Regular') or contains(text(), 'Extra'))]"
        
        try:
            wait.until(EC.presence_of_element_located((By.XPATH, xpath_linha)))
            elementos = driver.find_elements(By.XPATH, xpath_linha)
        except:
            print("❌ Tempo esgotado: A lista de diários não carregou.")
            return None, None
        
        print(f"📋 Encontrados {len(elementos)} itens. Filtrando 2026...")
        
        alvo = None
        # Pega o primeiro item da lista que seja de 2026 (assumindo ordem decrescente do site)
        for elem in elementos:
            if "2026" in elem.text:
                alvo = elem
                break
        
        if not alvo and elementos:
            print("⚠️ Nenhuma edição de 2026 encontrada. Pegando a mais recente disponível.")
            alvo = elementos[0]

        if alvo:
            print(f"🎯 Alvo Selecionado: '{alvo.text}'")
            
            # Clica para ativar a sessão e gerar a URL
            driver.execute_script("arguments[0].click();", alvo)
            time.sleep(5) # Espera breve para URL atualizar
            
            url_atual = driver.current_url
            id_diario = url_atual.split("/")[-1] if "/diario/" in url_atual else None
            
            if id_diario and id_diario.isdigit():
                link_api = f"https://atos.teresopolis.rj.gov.br/api/editions/download/{id_diario}"
                print(f"--- ETAPA 3: DOWNLOAD DO ARQUIVO (ID: {id_diario}) ---")
                
                # Roubo de Cookies para Autenticação
                selenium_cookies = driver.get_cookies()
                session = requests.Session()
                for cookie in selenium_cookies:
                    session.cookies.set(cookie['name'], cookie['value'])
                
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": url_portal
                }
                
                print("⬇️ Baixando...")
                # timeout=60 é CRUCIAL para não travar o robô se o servidor falhar
                response = session.get(link_api, headers=headers, stream=True, verify=False, timeout=60)
                
                if response.status_code == 200:
                    with open(caminho_pdf, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    tamanho = os.path.getsize(caminho_pdf)
                    print(f"📦 Download concluído. Tamanho: {tamanho} bytes")
                    
                    if tamanho > 3000: # Validando tamanho mínimo (3KB)
                        return caminho_pdf, url_atual
                    else:
                        print("❌ Arquivo muito pequeno (provavelmente corrompido ou erro de login).")
                else:
                    print(f"❌ Erro no servidor: Código {response.status_code}")
            else:
                print("❌ Não foi possível capturar o ID da edição.")
        else:
            print("❌ Nenhuma edição encontrada na página.")
            
        return None, None

    except Exception as e:
        print(f"❌ ERRO CRÍTICO: {e}")
        return None, None
    finally:
        if driver:
            driver.quit()

# --- 3. EXTRATOR ---
def extrair_texto(caminho):
    print("--- ETAPA 4: EXTRAÇÃO DE TEXTO ---")
    try:
        text = ""
        with pdfplumber.open(caminho) as pdf:
            # Limita a 5 páginas para não estourar memória se o PDF for gigante
            for page in pdf.pages[:10]: 
                text += page.extract_text() or ""
        return text[:100000]
    except Exception as e:
        print(f"❌ Erro ao ler PDF: {e}")
        return ""

# --- 4. IA ---
def analisar(texto):
    print("--- ETAPA 5: ANÁLISE IA ---")
    prompt = """
    Você é um monitor de Licitações. Analise o texto do Diário Oficial de Teresópolis.
    
    O QUE BUSCAR:
    - Licitações, Pregões, Tomadas de Preço, Chamamentos Públicos.
    - Contratos assinados de alto valor.
    - Oportunidades comerciais para empresas.
    
    O QUE IGNORAR:
    - Nomeações, Exonerações, Férias, Licenças médicas, Decretos de Ponto Facultativo.
    
    FORMATO DE SAÍDA (Se encontrar algo):
    🚨 **[Nicho]** (Ex: Obras, TI, Alimentos)
    📦 **Objeto:** Resumo do que é.
    💰 **Valor:** R$ X (se disponível)
    
    FORMATO DE SAÍDA (Se não houver NADA relevante):
    Responda apenas: "ND"
    """
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": texto}],
            temperature=0.3
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"Erro IA: {e}")
        return "ND"

# --- 5. TELEGRAM ---
def enviar_telegram(msg, link):
    print("--- ETAPA 6: ENVIO TELEGRAM ---")
    
    # Se a mensagem for ND, montamos um relatório de "Nada Consta"
    if not msg or "ND" in msg or len(msg) < 5:
        texto = (
            f"📊 *Monitor Teresópolis*\n"
            f"✅ Monitoramento finalizado.\n"
            f"ℹ️ Nenhuma oportunidade comercial identificada hoje.\n"
            f"🔗 [Acessar Documento]({link})"
        )
    else:
        texto = (
            f"📊 *Monitor Teresópolis*\n"
            f"🚀 *Oportunidades Encontradas!*\n\n"
            f"{msg}\n\n"
            f"🔗 [Baixar Edital]({link})"
        )
        
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": texto,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }, timeout=10)
        print("✅ Mensagem enviada!")
    except Exception as e:
        print(f"❌ Falha no envio: {e}")

def main():
    pdf, link = buscar_e_baixar_diario()
    if pdf and link:
        texto = extrair_texto(pdf)
        if len(texto) > 100:
            resumo = analisar(texto)
            enviar_telegram(resumo, link)
            print("🏁 PROCESSO CONCLUÍDO COM SUCESSO.")
        else:
            print("⚠️ O PDF foi baixado, mas parece estar vazio ou ser apenas imagem.")
            # Opcional: Avisar no telegram que houve erro de leitura
    else:
        print("❌ PROCESSO ENCERRADO COM FALHA NO DOWNLOAD.")

if __name__ == "__main__":
    main()
