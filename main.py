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
    caminho_pdf = "diario_hoje.pdf" if os.name == 'nt' else "/tmp/diario_hoje.pdf"
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
        # Pega todos os itens e tenta encontrar o mais recente (maior número de edição)
        elementos = driver.find_elements(By.XPATH, xpath_linha)
        melhor_candidato = None
        maior_edicao = 0
        
        print(f"📋 Analisando {len(elementos)} edições encontradas...")
        
        for elem in elementos:
            texto = elem.text
            # Ex: "Edição 22 / Ano 11..."
            if "Edição" in texto and "202" in texto: # Filtra ano atual
                try:
                    # Extrai o número da edição para comparar
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
            id_diario = url_atual.split("/")[-1] if "/diario/" in url_
